import os, ast, json
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','audit_settings')
import django; django.setup()
from django.db import connection
from django.core.management import call_command
from django.test.runner import DiscoverRunner
assert connection.settings_dict['NAME']=='audit_astra_1013_3e3c4376'
wanted={
 'test_weighted_average_idempotency_and_per_warehouse_cost',
 'test_concurrent_first_receipts_create_one_balance_and_stale_preview_is_rejected',
 'test_concurrent_releases_cannot_exceed_one_hold_remaining_amount',
 'test_second_line_failure_rolls_back_everything_and_key_can_retry',
 'test_preview_requires_manual_ambiguous_product_and_barcode_confirmation_then_confirms_once',
 'test_payment_requires_images_and_reason_for_difference_then_is_idempotent',
 'test_account_responsibility_and_pause_survive_re_registration',
 'test_private_payment_account_is_listed_but_only_owner_verifies',
 'test_payment_upload_validates_content_and_payment_image_access_is_private',
 'test_actual_cost_uses_current_weighted_average_and_empty_cost_clears',
 'test_freight_updates_margin_versions_and_commission_re_review_delta',
 'test_postgresql_problem_and_ship_race_never_leaves_partial_state',
 'test_lost_change_all_scope_blocks_exact_payment_replay_on_next_request',
 'test_cost_correction_replays_order_and_delivery_costs',
 'test_cross_warehouse_split_correction_updates_downstream_sale',
 'test_historical_restriction_release_passes_but_real_negative_stock_fails',
 'test_opening_count_cost_supplement_is_approved_with_correction',
 'test_corrected_purchase_recalculates_approved_count',
 'test_repeated_corrections_update_approved_commission_by_each_delta',
 'test_cost_preview_uses_one_repeatable_read_snapshot_and_expires_after_new_purchase',
 'test_postgresql_competing_gifts_cannot_overdraw_and_correction_race_expires_one_preview',
 'test_cost_correction_failure_rolls_back_source_inventory_and_approval',
 'test_split_gift_idempotency_and_transaction_rollback',
 'test_damage_exchange_refund_partial_retry_and_overflow',
 'test_stock_count_approval_quantity_anchor_and_owner_self_approval',
}
labels=[]
for path in Path('crm').glob('test*.py'):
 for c in ast.parse(path.read_text()).body:
  if isinstance(c,ast.ClassDef):
   for f in c.body:
    if isinstance(f,ast.FunctionDef) and f.name in wanted: labels.append(f'crm.{path.stem}.{c.name}.{f.name}')
assert len(labels)==len(wanted),(len(labels),len(wanted))
Path('../selected-tests.json').write_text(json.dumps(labels,indent=2))
call_command('flush',interactive=False,verbosity=0)
class OwnedDatabaseRunner(DiscoverRunner):
 def setup_databases(self,**kwargs): return []
 def teardown_databases(self,old_config,**kwargs): pass
runner=OwnedDatabaseRunner(verbosity=1,interactive=False)
raise SystemExit(runner.run_tests(labels))
