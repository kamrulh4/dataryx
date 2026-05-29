# core/core/subscriptions.py

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class SubscriptionPlan:
    plan_id: str
    product_name: str
    display_name: str
    max_conversions: Optional[int]
    max_file_size_mb: int
    default_duration_days: int
    paypal_link: Optional[str] = None
    doc_id: str = "Dataryx"


SUBSCRIPTION_PLANS: Dict[str, SubscriptionPlan] = {
    "free": SubscriptionPlan(
        plan_id="free",
        product_name="free",
        display_name="Free Plan",
        max_conversions=20,
        max_file_size_mb=2,
        default_duration_days=365,
        paypal_link=None,
        doc_id="Dataryx",
    ),
    "basic": SubscriptionPlan(
        plan_id="basic",
        product_name="Dataryx-basic-plan",
        display_name="Dataryx Basic Plan",
        max_conversions=50,
        max_file_size_mb=10,
        default_duration_days=30,
        paypal_link="https://www.paypal.com/webapps/billing/plans/subscribe?plan_id=P-0KB98147CY755271SNDYPGNQ",
        doc_id="Dataryx",
    ),
    "standard": SubscriptionPlan(
        plan_id="standard",
        product_name="Dataryx-standard-plan",
        display_name="Dataryx Standard Plan",
        max_conversions=None,
        max_file_size_mb=50,
        default_duration_days=30,
        paypal_link="https://www.paypal.com/webapps/billing/plans/subscribe?plan_id=P-8D43999168641563XNG2WNPY",
        doc_id="Dataryx",
    ),
}


PLAN_BY_PRODUCT: Dict[str, SubscriptionPlan] = {
    plan.product_name: plan for plan in SUBSCRIPTION_PLANS.values()
}

DEFAULT_PLAN_ID = "free"
