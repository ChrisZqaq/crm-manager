import os,json
os.environ.setdefault('DJANGO_SETTINGS_MODULE','audit_settings')
import django; django.setup()
from django.db import connection, close_old_connections
assert connection.settings_dict['NAME']=='audit_astra_1013_3e3c4376'
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group,Permission
from django.test import Client
from crm.permissions import ROLE_WAREHOUSE
from crm.models import *
from crm.test_phase5 import Phase5Base
from crm.inventory_adjustments import create_stock_count
from uuid import uuid4
f=Phase5Base(); U=get_user_model(); f.User=U
for attr,name in [('user','warehouse-test'),('owner','owner-test'),('sales','sales-one')]: setattr(f,attr,U.objects.get(username=name))
f.sku=SKU.objects.get(code='TST-0001'); f.warehouse=Warehouse.objects.get(name='Synthetic Warehouse'); f.channel=Channel.objects.get(name='Synthetic Online')
b=U.objects.create_user(username='audit-second-counter',password='synthetic-only'); b.groups.add(Group.objects.get(name=ROLE_WAREHOUSE)); b.user_permissions.add(Permission.objects.get(content_type__app_label='crm',codename='approve_stockcount'))
created=create_stock_count(f.user,{'reason':'Synthetic count by A','lines':[{'warehouse_id':f.warehouse.pk,'sku_id':f.sku.pk,'observed_quantity':8}]},uuid4()); count=StockCount.objects.get(pk=created['stock_count_id'])
c=Client(); c.force_login(b)
body={'operation_key':str(uuid4()),'expected_version':count.version,'reason':'Synthetic B rechecked and changed quantity','lines':[{'warehouse_id':f.warehouse.pk,'sku_id':f.sku.pk,'observed_quantity':7}]}
rev=c.patch(f'/api/v1/stock-counts/{count.pk}/update',data=json.dumps(body),content_type='application/json',HTTP_HOST='localhost'); count.refresh_from_db()
body={'operation_key':str(uuid4()),'expected_version':count.version,'balance_versions':{str(line.pk):line.balance.version for line in count.lines.select_related('balance')}}
app=c.post(f'/api/v1/stock-counts/{count.pk}/approve',data=json.dumps(body),content_type='application/json',HTTP_HOST='localhost')
result={'count_reviser_self_approval':{'revise_status':rev.status_code,'approve_status':app.status_code,'original_submitter_id':count.submitted_by_id,'reviser_and_approver_id':b.pk,'owner_role':b.groups.filter(name='老板').exists(),'on_hand_after':InventoryBalance.objects.get(warehouse=f.warehouse,sku=f.sku).on_hand}}
# Real two-connection expiry-boundary interleaving. Start registration while image is valid;
# pause after validation, then cleanup evaluates just after the 24-hour boundary.
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.management import call_command
from crm import fulfillment
from crm.management.commands import cleanup_orphan_receipt_images as cleanup
from io import StringIO
order=f.new_order(sale_price='20.000'); image=ReceiptImage(owner=f.sales,image_format='PNG',size=1); image.file.save('audit.image',ContentFile(b'x'))
t0=timezone.now(); ReceiptImage.objects.filter(pk=image.pk).update(created_at=t0-timedelta(hours=24)+timedelta(seconds=1))
validated=Event(); cleaned=Event(); original=fulfillment._images_for_payment
name=image.file.name; storage=image.file.storage
payload={'operation_key':str(uuid4()),'expected_version':order.version,'account_id':PaymentAccount.objects.first().pk,'received_amount':str(order.revenue_amount),'image_ids':[image.pk]}
def pause(*args):
 images=original(*args); validated.set(); assert cleaned.wait(10); return images
def register():
 close_old_connections()
 try: return fulfillment.register_payment(U.objects.get(pk=f.sales.pk),order.pk,payload)
 finally: close_old_connections()
with patch.object(fulfillment,'_images_for_payment',side_effect=pause):
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(register); assert validated.wait(10)
  with patch.object(cleanup,'timezone',SimpleNamespace(now=lambda:t0+timedelta(seconds=2))):
   out=StringIO(); call_command('cleanup_orphan_receipt_images',stdout=out)
  cleaned.set(); reg=future.result(timeout=10)
order.refresh_from_db()
result['image_cleanup_registration_race']={'cleanup':out.getvalue().strip(),'registration_status':reg['status'],'order_status':order.status,'payment_image_count':order.payment_record.images.count(),'file_exists':storage.exists(name)}
print(json.dumps(result,ensure_ascii=False,indent=2))
