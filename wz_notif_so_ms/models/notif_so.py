# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import datetime
import pytz
import logging

_logger = logging.getLogger(__name__)

class NotifSalesMs(models.Model):
	_inherit = 'sale.order'

	manager_approved = fields.Boolean(string='Manager Approved', default=False)


	def copy(self, default=None):
		"""Override method copy agar manager_approved selalu bernilai False saat SO diduplikasi."""
		default = dict(default or {}, manager_approved=False)
		return super().copy(default)

	def action_manager_approve(self):
		for order in self:
			order.manager_approved = True
			user_name = self.env.user.name
			order.message_post(body=_(f"Sale Order ini disetujui oleh {user_name}"))
		return True

	@api.model
	def get_pending_manager_approval_count(self):
		"""Query ringan untuk menghitung SO dari 2 hari lalu s.d hari ini dengan DO belum done"""
		if not self.env.user.has_group('wz_notif_so_ms.group_sale_manager_notif'):
			return {'count': 0, 'interval': 0}

		# Mengambil nilai interval dari ir.config_parameter
		param_interval = self.env['ir.config_parameter'].sudo().get_param('interval_notif_so')
		
		# Jika parameter belum diset atau kosong, kembalikan interval 0 agar tidak tampil
		if not param_interval or not param_interval.isdigit():
			return {'count': 0, 'interval': 0}

		interval_ms = int(param_interval) * 1000  # Konversi detik ke milidetik untuk JS

		user_tz = pytz.timezone('Asia/Jakarta')
		now_local = datetime.datetime.now(user_tz)
		
		start_local_dt = (now_local - datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
		end_local_dt = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
		
		start_utc_dt = start_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
		end_utc_dt = end_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
		
		query = """
			SELECT count(so.id) 
			FROM sale_order so
			WHERE so.state = 'sale' 
			  AND (so.manager_approved = FALSE OR so.manager_approved IS NULL)
			  AND so.date_order >= %s
			  AND so.date_order <= %s
			  AND EXISTS (
				  SELECT 1 FROM stock_picking sp 
				  WHERE sp.sale_id = so.id 
				    AND sp.state != 'done'
			  )
		"""
		self.env.cr.execute(query, (start_utc_dt, end_utc_dt))
		res = self.env.cr.fetchone()
		count = res[0] if res else 0

		return {'count': count, 'interval': interval_ms}

	@api.model
	def get_pending_manager_approval_action_id(self):
		"""Membuat ir.actions.act_window secara dinamis dengan rentang 2 hari lalu s.d hari ini & DO belum done"""
		user_tz = pytz.timezone('Asia/Jakarta')
		now_local = datetime.datetime.now(user_tz)
		
		start_local_dt = (now_local - datetime.timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
		end_local_dt = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
		
		start_utc_dt = start_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
		end_utc_dt = end_local_dt.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

		current_date_str = now_local.strftime('%d-%m-%Y')

		# Bersihkan record ir.actions.act_window pending approval yang lama agar database tidak menumpuk
		old_actions = self.env['ir.actions.act_window'].sudo().search([
			('name', 'like', 'Pending Manager Approval SO%'),
			('name', 'not like', f'Pending Manager Approval SO ({current_date_str})%')
		])
		if old_actions:
			old_actions.unlink()

		domain_list = [
			('state', '=', 'sale'),
			('manager_approved', '=', False),
			('date_order', '>=', start_utc_dt),
			('date_order', '<=', end_utc_dt),
			('picking_ids.state', '!=', 'done')
		]

		action_vals = {
			'name': f'Pending Manager Approval SO ({current_date_str})',
			'res_model': 'sale.order',
			'view_mode': 'tree,form',
			'views': [(False, 'list'), (False, 'form')],
			'domain': str(domain_list),
			'target': 'current',
		}
		
		# Membuat record action window baru di database secara dinamis
		action = self.env['ir.actions.act_window'].sudo().create(action_vals)
		return action.id