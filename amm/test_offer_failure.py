#!/usr/bin/env python3
import math

def get_fee(tfee: float) -> float:
    return tfee / 100_000

def fee_mult(tfee: float) -> float:
    return 1 - get_fee(tfee)

def fee_mult_half(tfee: float) -> float:
    return 1 - get_fee(tfee) / 2

def solve_quadratic_eq_smallest(a: float, b: float, c: float) -> float:
    d = b * b - 4 * a * c
    if d < 0:
        return None
    if b > 0:
        return (2 * c) / (-b - math.sqrt(d))
    else:
        return (2 * c) / (-b + math.sqrt(d))

def swap_asset_out(pool_in: float, pool_out: float, taker_gets: float, tfee: float) -> float:
    return ((pool_in * pool_out) / (pool_out - taker_gets) - pool_in) / fee_mult(tfee)

def reduce_offer(amount: float) -> float:
    return amount * 0.9999

def change_spq(pool_in : float, pool_out: float, target_quality: float, tfee: float) -> (float, float):
    if target_quality == 0:
        return None

    f = fee_mult(tfee)
    a = 1
    b = pool_in * (1 - 1 / f) * target_quality - 2 * pool_out
    c = pool_out * pool_out - (pool_in * pool_out) * target_quality

    taker_gets = solve_quadratic_eq_smallest(a, b, c)
    if taker_gets is None or taker_gets <= 0 or taker_gets == pool_out:
        return None

    taker_gets_constraint = pool_out - pool_in * target_quality * f
    if taker_gets_constraint <= 0:
        return None

    if taker_gets_constraint < taker_gets:
        taker_gets = taker_gets_constraint

    def get_amounts (taker_gets_proposed):
        taker_gets = taker_gets_proposed
        return (swap_asset_out(pool_in, pool_out, taker_gets, tfee), taker_gets)

    amounts = get_amounts(taker_gets)
    if (amounts[1]/amounts[0]) < target_quality:
        return get_amounts(reduce_offer(amounts[1]))
    else:
        return amounts

pool_out = 12_239_086_148
pool_in = 81_705_447.27387387
pool_out = 1_321_727_587
pool_in = 522_044.0842022011
pool_spq = pool_out / pool_in
tfee = 0

for i in range(0, 15):
    taker_pays = 1_000_000_000_000_000e+5 / 10**i
    taker_gets = 1_000_000
    target_quality = taker_gets / taker_pays
    amounts = change_spq(pool_in, pool_out, target_quality, tfee)
    if amounts is not None:
        print (i, 100 * target_quality / pool_spq)
        print ('offer', amounts[1], amounts[0])
        print ('pool ', pool_out, pool_in)
        print ('pct pool out', 100 * amounts[1]/pool_out)
        exit(0)