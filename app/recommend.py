"""Rank safe, plain-word actions. Recommendations never place orders.

Story text is deliberately not used as evidence: only the computed Facts are.
"""
from .schemas import Action


def _proof(facts, brand, *fields):
    lines = []
    for field in fields:
        key = f'{brand.name}.{field}'
        if key in facts.fact_ids:
            label = {
                'stock_kg': 'stock in kg', 'avg_daily_out_kg': 'average sent out each day in kg',
                'days_left': 'days left', 'days_left_low': 'days left at the faster pace',
                'days_left_high': 'days left at the slower pace',
                'weeks_of_stock': 'weeks of stock',
                'out_last_7d_kg': 'sent out in the recent week in kg',
                'out_prior_7d_kg': 'sent out in the week before in kg',
            }[field]
            lines.append(f'{brand.name}: {label} = {facts.fact_ids[key]} [fact: {key}]')
    return lines


def _stock_action(facts, brand):
    if brand.status == 'running_out':
        why = f'{brand.name} is getting low. Check the next delivery before promising more stock.'
        if brand.days_left_low is not None and brand.days_left_high is not None:
            why = (f'{brand.name} has about {brand.days_left_low} to '
                   f'{brand.days_left_high} days left at the recent pace.')
        elif brand.days_left is not None:
            why = f'{brand.name} has about {brand.days_left} days left at the recent pace.'
        return Action(
            title=f'Check a new load of {brand.name}', why=why,
            expected_effect='Helps avoid running out before the next load arrives. Confirm the delivery time and amount first.',
            proof=_proof(facts, brand, 'stock_kg', 'days_left', 'days_left_low', 'days_left_high'),
        )
    if brand.status == 'piling_up':
        return Action(
            title=f'Hold off on more {brand.name}',
            why=f'{brand.name} has about {brand.weeks_of_stock} weeks of stock at the recent pace.',
            expected_effect='Keeps more money and storage space free while this stock sells. Check pending orders before changing any purchase.',
            proof=_proof(facts, brand, 'stock_kg', 'weeks_of_stock', 'avg_daily_out_kg'),
        )
    return Action(
        title=f'Keep an eye on {brand.name}',
        why=f'{brand.name} is moving without being low or piling up at the recent pace.',
        expected_effect='Helps you notice a change in demand before it becomes a shortage or excess stock.',
        proof=_proof(facts, brand, 'stock_kg', 'avg_daily_out_kg', 'days_left'),
    )


def recommend(facts, story):
    """Exactly three cards with data, otherwise the single learning card."""
    usable = [brand for brand in facts.brands if brand.status != 'no_data']
    if not usable:
        return [Action(
            title='Add what you sold so I can learn',
            why='There are not enough separate days of sales to work out how fast stock moves.',
            expected_effect='Adding daily sales helps work out which brands need another load.',
            proof=['Total stock = ' + str(facts.total_stock_kg) + ' kg [fact: total_stock_kg]'],
        )]

    def rank(brand):
        if brand.status == 'running_out':
            return (0, brand.days_left if brand.days_left is not None else -1, brand.brand_id)
        if brand.status == 'piling_up':
            return (1, -(brand.weeks_of_stock or 0), brand.brand_id)
        return (2, brand.days_left if brand.days_left is not None else float('inf'), brand.brand_id)

    ordered = sorted(usable, key=rank)
    actions = [_stock_action(facts, brand) for brand in ordered[:3]]
    padding = [
        Action(title='Check the loads already on the way',
               why='A delivery already arranged may change which brand needs attention.',
               expected_effect='Helps avoid buying the same stock twice. Record each load when it arrives.',
               proof=['Total stock = ' + str(facts.total_stock_kg) + ' kg [fact: total_stock_kg]']),
        Action(title='Record what went out each day',
               why='Missing sales can make stock appear to last longer than it will.',
               expected_effect='Keeps the stock count and days left closer to what is happening in the mill.',
               proof=_proof(facts, ordered[0], 'out_last_7d_kg', 'out_prior_7d_kg')),
    ]
    return (actions + padding)[:3]
