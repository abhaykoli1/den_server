"""Default seller subscription catalog. Existing rows are never overwritten."""
from .util import now_iso


def default_seller_plans():
    now = now_iso()
    tiers = [
        ("sp_starter", "Starter Monthly", 499, "monthly", 30, 7, 1, 1),
        ("sp_starter_yearly", "Starter Annual", 4999, "yearly", 365, 7, 1, 2),
        ("sp_pro", "Pro Monthly", 999, "monthly", 30, 14, 3, 3),
        ("sp_annual", "Pro Annual", 9999, "yearly", 365, 14, 3, 4),
        ("sp_business", "Business Monthly", 1999, "monthly", 30, 14, 10, 5),
        ("sp_business_yearly", "Business Annual", 19999, "yearly", 365, 14, 10, 6),
    ]
    shared = ["Tables & live billing", "Players, memberships & dues", "Inventory and item sales"]
    pro = shared + ["Finance and daily reports", "Tournaments", "Staff permissions", "Bookings"]
    business = pro + ["Up to 10 clubs", "Priority support", "Advanced utilization reports"]
    result = []
    for plan_id, name, price, cycle, days, trial, max_clubs, order in tiers:
        features = shared if max_clubs == 1 else pro if max_clubs == 3 else business
        result.append({
            "id": plan_id, "name": name,
            "description": f"{name} subscription for snooker and sports clubs",
            "price": price, "billingCycle": cycle, "durationDays": days,
            "trialDays": trial, "maxClubs": max_clubs,
            "features": features, "active": True,
            "recommended": plan_id == "sp_pro", "sortOrder": order,
            "createdAt": now,
        })
    return result


async def ensure_seller_plan_catalog(db):
    """Create missing defaults without changing any existing administrator choice."""
    for plan in default_seller_plans():
        await db.subscription_plans.update_one(
            {"id": plan["id"]}, {"$setOnInsert": plan}, upsert=True)
