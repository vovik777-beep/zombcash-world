"""Unknown is distinct from false: absence of evidence is not evidence of absence."""
def social_presence(links):
    if links is None:
        return None
    return any(isinstance(x, dict) and x.get("url") for x in links)

def promotion_state(ads, boosts, orders):
    paid = None if ads is None and orders is None else bool(ads or orders)
    boosted = None if boosts is None else bool(boosts)
    return paid, boosted
