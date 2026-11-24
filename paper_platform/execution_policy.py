def normalize_order_legs(rule, policy):
    """Return a policy with explicit open and close order instructions."""
    policy = dict(policy)
    if type(policy["contracts"]) is not int or policy["contracts"] <= 0:
        raise ValueError(f"invalid contract quantity for {rule}")
    for leg in ("open", "close"):
        instruction = policy[leg]
        if isinstance(instruction, str):
            instruction = {"style": instruction}
        if not isinstance(instruction, dict) or instruction.keys() - {"style", "offset_ticks"}:
            raise ValueError(f"unknown order instruction for {rule} {leg}")
        style = instruction.get("style")
        offset = instruction.get("offset_ticks", 0)
        if (style not in ("post", "cross", "cross_limit") or type(offset) is not int
                or offset < 0 or (style == "cross" and offset)):
            raise ValueError(f"invalid order instruction for {rule} {leg}")
        policy[leg] = {"style": style, "offset_ticks": offset}
    return policy
