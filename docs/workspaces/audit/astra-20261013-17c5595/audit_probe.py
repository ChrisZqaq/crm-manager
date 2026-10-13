import os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','audit_settings')
import django
django.setup()
from django.db import connection
assert connection.settings_dict['NAME']=='audit_astra_1013_3e3c4376'
from uuid import uuid4
from crm.test_phase5 import Phase5Base
from crm.fulfillment import review_commission, update_outbound_freight
from crm.corrections import preview_cost_correction, submit_cost_correction
from crm.models import CommissionReview, CostCorrection, SalesOrder, BusinessOperation
from crm.reports import _commission_status
from crm.sales import preview_sales_order, confirm_sales_order
from django.contrib.auth.models import Permission, Group
from django.test import Client
from crm.permissions import ROLE_OWNER
f=Phase5Base(); f.setUp()
r=f.receive(quantity_value=10, price='10.000')
o=f.new_order(sale_price='20.000'); f.fulfill_onsite(o)
review_commission(f.owner,o.pk,{'operation_key':str(uuid4()),'expected_version':o.version})
def state(o):
 o.refresh_from_db(); review=CommissionReview.objects.get(order=o)
 return {'amount_version':o.amount_version,'approved_version':review.approved_amount_version,'estimate':str(o.commission_estimate),'approved':str(review.approved_amount),'report_state':_commission_status(o,review)[0]}
result={'commission_initial':state(o)}
update_outbound_freight(f.user,o.pk,{'operation_key':str(uuid4()),'expected_version':o.version,'outbound_freights':[{'line_id':o.lines.get(active=True).pk,'amount':'10.000'}]})
result['commission_pending_freight']=state(o)
p=preview_cost_correction(f.user,f.correction_data(r,purchase_price='11.000'))
s=submit_cost_correction(f.user,uuid4(),p['preview_token'])
f.approve_correction(CostCorrection.objects.get(pk=s['cost_correction_id']))
result['commission_after_correction']=state(o)
# A successful amendment, followed by withdrawal of change-all scope.
o2=f.new_order(sale_price='20.000'); data=f.amendment_payload(o2); data['remarks']='Synthetic amended remark'
p=preview_sales_order(f.owner,data,order_id=o2.pk); key=str(uuid4()); confirm_sales_order(f.owner,key,p['preview_token'])
g=Group.objects.get(name=ROLE_OWNER); perm=Permission.objects.get(content_type__app_label='crm',codename='change_all_sales_orders'); g.permissions.remove(perm)
c=Client(); c.force_login(f.User.objects.get(pk=f.owner.pk))
body={'operation_key':key,'preview_token':p['preview_token']}; before=BusinessOperation.objects.count()
resp=c.post(f'/api/v1/orders/{o2.pk}/amend',data=json.dumps(body),content_type='application/json',HTTP_HOST='localhost')
result['amend_replay_after_scope_revocation']={'status':resp.status_code,'body':resp.json(),'operation_delta':BusinessOperation.objects.count()-before}
print(json.dumps(result,ensure_ascii=False,indent=2))
