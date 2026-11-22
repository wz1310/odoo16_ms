import datetime
import json
import math
import requests
import pytz  # <-- Tambahkan import pytz untuk konversi zona waktu
import logging
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)

class ScheduleTeleMs(models.Model):
	_inherit = 'sale.order'


	def send_schedule_resume_tele(self):
		"""
		Fungsi untuk mengirimkan resume Sales Order harian (dari 2 hari lalu s.d hari ini)
		dengan DO yang belum done, lengkap dengan tombol inline URL ke Odoo.
		"""
		_logger.info("=== MULAI PROSES: send_schedule_resume_tele ===")
		
		# 1. Ambil konfigurasi URL dan Chat ID dari System Parameters
		url_tele = self.env['ir.config_parameter'].sudo().search([('key', '=', 'url_tele_notif_sales')]).value
		chatid_number = self.env['ir.config_parameter'].sudo().search([('key', '=', 'chatid_sales')]).value
		
		if not url_tele or not chatid_number:
			_logger.warning("send_schedule_resume_tele BATAL: Config url_tele_notif_sales / chatid_sales tidak ditemukan!")
			return # Keluar jika konfigurasi belum diset

		# 2. Tentukan rentang waktu dari 2 hari lalu (00:00:00) s.d hari ini (23:59:59 WIB) dikonversi ke UTC
		user_tz = pytz.timezone('Asia/Jakarta')
		now_local = datetime.datetime.now(user_tz)
		
		start_local_dt = (now_local - datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
		end_local_dt = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
		
		start_utc_dt = start_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
		end_utc_dt = end_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

		_logger.info("Filter Tanggal (UTC) send_schedule_resume_tele: %s s.d %s", start_utc_dt, end_utc_dt)

		# 3. Eksekusi Query SQL dengan filter rentang 2 hari dan DO belum done
		query = """
			SELECT 
				rp.name AS customer_name,
				COALESCE(SUM(sol.product_uom_qty), 0.0) AS total_qty
			FROM sale_order so
			JOIN res_partner rp ON so.partner_id = rp.id
			JOIN sale_order_line sol ON sol.order_id = so.id
			WHERE so.state = 'sale'
			  AND so.date_order >= %s
			  AND so.date_order <= %s
			  AND EXISTS (
				  SELECT 1 FROM stock_picking sp 
				  WHERE sp.sale_id = so.id 
					AND sp.state != 'done'
			  )
			GROUP BY rp.name
			ORDER BY rp.name;
		"""
		self.env.cr.execute(query, (start_utc_dt, end_utc_dt))
		results = self.env.cr.fetchall()

		_logger.info("Hasil Query send_schedule_resume_tele (%d record): %s", len(results) if results else 0, results)

		if not results:
			_logger.info("send_schedule_resume_tele SELESAI: Tidak ada data SO pada rentang tanggal tersebut.")
			return

		# 4. Susun bagian teks untuk masing-masing customer dan hitung total akumulasi SO
		customer_lines = ""
		total_akumulasi_so = 0.0

		for row in results:
			customer_name = row[0]
			total_qty = row[1]
			total_akumulasi_so += total_qty
			customer_lines += f"Total SO {customer_name} {total_qty}\n"

		# 5. Hitung Jumlah Kebutuhan Tenaga Kerja berdasarkan formula SO (mrp.worker.formula)
		# formula = self.env['mrp.worker.formula'].search([], limit=1)
		# jumlah_tenaga_kerja = 0
		# if formula and formula.qty_base > 0:
		# 	multiplier = math.ceil(total_akumulasi_so / formula.qty_base)
		# 	jumlah_tenaga_kerja = multiplier * formula.worker_count

		date_str = now_local.strftime('%d-%m-%Y')

		# 6. Menyusun template pesan Telegram
		message_text = (
			f"Resume SO {date_str}\n\n"
			f"{customer_lines}\n"
			f"Sehingga Total Akumulasi SO adalah {total_akumulasi_so}\n"
			# f"Jumlah Kebutuhan Tenaga Kerja {jumlah_tenaga_kerja}\n\n"
			f"Silahkan ditindak lanjuti!"
		)

		# 7. Ambil Base URL Odoo & Buat Action Window Dinamis
		base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
		base_url = base_url.replace('http://', 'https://')
		
		payload = {
			"chat_id": chatid_number,
			"text": message_text,
			"parse_mode": "HTML"
		}

		if base_url and 'localhost' not in base_url and '127.0.0.1' not in base_url:
			domain_list = [
				('state', '=', 'sale'), 
				('date_order', '>=', start_utc_dt), 
				('date_order', '<=', end_utc_dt),
				('picking_ids.state', '!=', 'done')
			]
			context_dict = {'group_by': 'partner_id'}
			
			action_vals = {
				'name': f'Resume SO {date_str}',
				'res_model': 'sale.order',
				'view_mode': 'tree,kanban,form',
				'domain': str(domain_list),
				'context': str(context_dict),
				'target': 'current',
			}
			action = self.env['ir.actions.act_window'].sudo().create(action_vals)
			action_id = action.id
			
			odoo_so_url = f"{base_url.rstrip('/')}/web#action={action_id}&model=sale.order&view_type=list"
			
			payload["reply_markup"] = {
				"inline_keyboard": [
					[
						{
							"text": "Lihat List SO di Odoo",
							"url": odoo_so_url
						}
					]
				]
			}
		
		headers = {'Content-Type': 'application/json'}
		
		try:
			_logger.info("Mengirim payload Telegram send_schedule_resume_tele...")
			response = requests.post(url_tele, headers=headers, data=json.dumps(payload), timeout=10)
			_logger.info("Response send_schedule_resume_tele: status=%s, body=%s", response.status_code, response.text)
			response.raise_for_status()
		except Exception as e:
			_logger.error("Error Telegram Schedule: %s", str(e))
			pass

	def send_schedule_resume_approved_tele(self):
		"""
		Fungsi untuk mengirimkan resume Sales Order harian khusus yang SUDAH di-approve manager
		(dari 2 hari lalu s.d hari ini) dengan DO yang belum done.
		"""
		_logger.info("=== MULAI PROSES: send_schedule_resume_approved_tele ===")
		
		url_tele = self.env['ir.config_parameter'].sudo().search([('key', '=', 'url_tele_notif_sales')]).value
		chatid_number = self.env['ir.config_parameter'].sudo().search([('key', '=', 'chatid_sales')]).value
		
		if not url_tele or not chatid_number:
			_logger.warning("send_schedule_resume_approved_tele BATAL: Config url_tele_notif_sales / chatid_sales tidak ditemukan!")
			return

		user_tz = pytz.timezone('Asia/Jakarta')
		now_local = datetime.datetime.now(user_tz)
		
		start_local_dt = (now_local - datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
		end_local_dt = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
		
		start_utc_dt = start_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
		end_utc_dt = end_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

		_logger.info("Filter Tanggal (UTC) send_schedule_resume_approved_tele: %s s.d %s", start_utc_dt, end_utc_dt)

		# Eksekusi Query SQL dengan manager_approved, rentang 2 hari, dan DO belum done
		query = """
			SELECT 
				rp.name AS customer_name,
				COALESCE(SUM(sol.product_uom_qty), 0.0) AS total_qty
			FROM sale_order so
			JOIN res_partner rp ON so.partner_id = rp.id
			JOIN sale_order_line sol ON sol.order_id = so.id
			WHERE so.state = 'sale'
			  AND so.manager_approved = TRUE
			  AND so.date_order >= %s
			  AND so.date_order <= %s
			  AND EXISTS (
				  SELECT 1 FROM stock_picking sp 
				  WHERE sp.sale_id = so.id 
					AND sp.state != 'done'
			  )
			GROUP BY rp.name
			ORDER BY rp.name;
		"""
		self.env.cr.execute(query, (start_utc_dt, end_utc_dt))
		results = self.env.cr.fetchall()

		_logger.info("Hasil Query send_schedule_resume_approved_tele (%d record): %s", len(results) if results else 0, results)

		if not results:
			_logger.info("send_schedule_resume_approved_tele SELESAI: Tidak ada data SO approved pada rentang tanggal tersebut.")
			return

		customer_lines = ""
		total_akumulasi_so = 0.0

		for row in results:
			customer_name = row[0]
			total_qty = row[1]
			total_akumulasi_so += total_qty
			customer_lines += f"Total SO {customer_name} {total_qty}\n"

		# formula = self.env['mrp.worker.formula'].search([], limit=1)
		# jumlah_tenaga_kerja = 0
		# if formula and formula.qty_base > 0:
		# 	multiplier = math.ceil(total_akumulasi_so / formula.qty_base)
		# 	jumlah_tenaga_kerja = multiplier * formula.worker_count

		date_str = now_local.strftime('%d-%m-%Y')

		message_text = (
			f"Resume SO Approved {date_str}\n\n"
			f"{customer_lines}\n"
			f"Sehingga Total Akumulasi SO Approved adalah {total_akumulasi_so}\n"
			# f"Jumlah Kebutuhan Tenaga Kerja {jumlah_tenaga_kerja}\n\n"
			f"Silahkan ditindak lanjuti!"
		)

		base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
		base_url = base_url.replace('http://', 'https://')
		
		payload = {
			"chat_id": chatid_number,
			"text": message_text,
			"parse_mode": "HTML"
		}

		if base_url and 'localhost' not in base_url and '127.0.0.1' not in base_url:
			domain_list = [
				('state', '=', 'sale'), 
				('manager_approved', '=', True), 
				('date_order', '>=', start_utc_dt), 
				('date_order', '<=', end_utc_dt),
				('picking_ids.state', '!=', 'done')
			]
			context_dict = {'group_by': 'partner_id'}
			
			action_vals = {
				'name': f'Resume SO Approved {date_str}',
				'res_model': 'sale.order',
				'view_mode': 'tree,kanban,form',
				'domain': str(domain_list),
				'context': str(context_dict),
				'target': 'current',
			}
			action = self.env['ir.actions.act_window'].sudo().create(action_vals)
			action_id = action.id
			
			odoo_so_url = f"{base_url.rstrip('/')}/web#action={action_id}&model=sale.order&view_type=list"
			
			payload["reply_markup"] = {
				"inline_keyboard": [
					[
						{
							"text": "Lihat List SO Approved di Odoo",
							"url": odoo_so_url
						}
					]
				]
			}
		
		headers = {'Content-Type': 'application/json'}
		
		try:
			_logger.info("Mengirim payload Telegram send_schedule_resume_approved_tele...")
			response = requests.post(url_tele, headers=headers, data=json.dumps(payload), timeout=10)
			_logger.info("Response send_schedule_resume_approved_tele: status=%s, body=%s", response.status_code, response.text)
			response.raise_for_status()
		except Exception as e:
			_logger.error("Error Telegram Schedule Approved: %s", str(e))
			pass

	def send_schedule_tag_product_resume_approved_tele(self):
		"""
		Fungsi untuk mengirimkan resume Sales Order harian dengan detail varian/produk
		berdasarkan Customer Tag khusus yang SUDAH di-approve manager (dari 2 hari lalu s.d hari ini)
		dengan DO yang belum done.
		"""
		_logger.info("=== MULAI PROSES: send_schedule_tag_product_resume_approved_tele ===")
		
		url_tele = self.env['ir.config_parameter'].sudo().search([('key', '=', 'url_tele_notif_sales')]).value
		chatid_number = self.env['ir.config_parameter'].sudo().search([('key', '=', 'chatid_sales_manager')]).value
		
		if not url_tele or not chatid_number:
			_logger.warning("send_schedule_tag_product_resume_approved_tele BATAL: Config url_tele_notif_sales / chatid_sales_manager tidak ditemukan!")
			return

		user_tz = pytz.timezone('Asia/Jakarta')
		now_local = datetime.datetime.now(user_tz)
		
		start_local_dt = (now_local - datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
		end_local_dt = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
		
		start_utc_dt = start_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
		end_utc_dt = end_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

		_logger.info("Filter Tanggal (UTC) send_schedule_tag_product_resume_approved_tele: %s s.d %s", start_utc_dt, end_utc_dt)

		# Query SQL dengan manager_approved, rentang 2 hari, dan DO belum done
		query = """
			SELECT 
				COALESCE(rpc.name->>'id_ID', rpc.name->>'en_US', rpc.name::text, 'Tanpa Tag') AS tag_name,
				COALESCE(pt.name->>'id_ID', pt.name->>'en_US', pt.name::text) AS product_name,
				COALESCE(SUM(sol.product_uom_qty), 0.0) AS total_qty
			FROM sale_order so
			JOIN res_partner rp ON so.partner_id = rp.id
			LEFT JOIN res_partner_res_partner_category_rel rel ON rp.id = rel.partner_id
			LEFT JOIN res_partner_category rpc ON rel.category_id = rpc.id
			JOIN sale_order_line sol ON sol.order_id = so.id
			JOIN product_product pp ON sol.product_id = pp.id
			JOIN product_template pt ON pp.product_tmpl_id = pt.id
			WHERE so.state = 'sale'
			  AND so.manager_approved = TRUE
			  AND (sol.display_type IS NULL OR sol.display_type = '')
			  AND so.date_order >= %s
			  AND so.date_order <= %s
			  AND EXISTS (
				  SELECT 1 FROM stock_picking sp 
				  WHERE sp.sale_id = so.id 
					AND sp.state != 'done'
			  )
			GROUP BY rpc.name, pt.name
			ORDER BY rpc.name, pt.name;
		"""
		self.env.cr.execute(query, (start_utc_dt, end_utc_dt))
		results = self.env.cr.fetchall()

		_logger.info("Hasil Query send_schedule_tag_product_resume_approved_tele (%d record): %s", len(results) if results else 0, results)

		if not results:
			_logger.info("send_schedule_tag_product_resume_approved_tele SELESAI: Tidak ada data produk SO approved berdasarkan tag pada rentang tanggal tersebut.")
			return

		from collections import defaultdict
		tag_products = defaultdict(list)
		total_akumulasi_so = 0.0

		for row in results:
			tag_name = row[0]
			prod_name = str(row[1]) if row[1] else '-'
			qty = row[2]
			tag_products[tag_name].append((prod_name, qty))
			total_akumulasi_so += qty

		# formula = self.env['mrp.worker.formula'].search([], limit=1)
		# jumlah_tenaga_kerja = 0
		# if formula and formula.qty_base > 0:
		# 	multiplier = math.ceil(total_akumulasi_so / formula.qty_base)
		# 	jumlah_tenaga_kerja = multiplier * formula.worker_count

		date_str = now_local.strftime('%d-%m-%Y')

		footer_text = (
			f"Sehingga Total Akumulasi SO Approved adalah {total_akumulasi_so}\n"
			# f"Jumlah Kebutuhan Tenaga Kerja {jumlah_tenaga_kerja}\n\n"
			f"Silahkan ditindak lanjuti!"
		)
		header_text = f"Resume SO Approved {date_str}\n\n"

		max_length = 3800
		messages = []
		current_chunk = header_text

		for tag_name, prod_list in tag_products.items():
			block = f"Akumulasi SO {tag_name}\n"
			for prod_name, qty in prod_list:
				block += f"Total {prod_name} {qty}\n"
			block += "\n"

			if len(current_chunk) + len(block) > max_length:
				messages.append(current_chunk)
				current_chunk = block
			else:
				current_chunk += block

		current_chunk += footer_text
		messages.append(current_chunk)

		base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
		base_url = base_url.replace('http://', 'https://')
		
		headers = {'Content-Type': 'application/json'}

		for idx, message_text in enumerate(messages):
			payload = {
				"chat_id": chatid_number,
				"text": message_text,
				"parse_mode": "HTML"
			}

			if idx == len(messages) - 1 and base_url and 'localhost' not in base_url and '127.0.0.1' not in base_url:
				domain_list = [
					('state', '=', 'sale'), 
					('manager_approved', '=', True), 
					('date_order', '>=', start_utc_dt), 
					('date_order', '<=', end_utc_dt),
					('picking_ids.state', '!=', 'done')
				]
				context_dict = {'group_by': 'partner_id'}
				
				action_vals = {
					'name': f'Resume SO Approved {date_str}',
					'res_model': 'sale.order',
					'view_mode': 'tree,kanban,form',
					'domain': str(domain_list),
					'context': str(context_dict),
					'target': 'current',
				}
				action = self.env['ir.actions.act_window'].sudo().create(action_vals)
				action_id = action.id
				
				odoo_so_url = f"{base_url.rstrip('/')}/web#action={action_id}&model=sale.order&view_type=list"
				
				payload["reply_markup"] = {
					"inline_keyboard": [
						[
							{
								"text": "Lihat List SO Approved di Odoo",
								"url": odoo_so_url
							}
						]
					]
				}

			try:
				_logger.info("Mengirim payload Telegram send_schedule_tag_product_resume_approved_tele (Bagian %d/%d)...", idx+1, len(messages))
				response = requests.post(url_tele, headers=headers, data=json.dumps(payload), timeout=10)
				_logger.info("Response send_schedule_tag_product_resume_approved_tele: status=%s, body=%s", response.status_code, response.text)
				response.raise_for_status()
			except Exception as e:
				_logger.error("Error Telegram Schedule Tag Product Resume Approved: %s", str(e))
				pass