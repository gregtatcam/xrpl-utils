#!/usr/bin/env python3

import math
import sys
from decimal import Decimal

def fee_mult(tfee):
    return 1 - tfee

def fee_multHalf(tfee):
    return 1 - tfee / 2

def solve_quadratic_eq(a: Decimal, b: Decimal, c: Decimal) -> Decimal:
    p = b * b - 4 * a * c
    return (-b + p.sqrt()) / (2 * a)

def get_lp_tokens(asset1_balance: Decimal, asset2_balance: Decimal) -> Decimal:
    return (asset1_balance * asset2_balance).sqrt()

# Equation 3
def lp_tokens_in(asset1_balance: Decimal, asset2_balance: Decimal, asset1_deposit: Decimal, tfee: Decimal) -> Decimal:
    lpt_balance = get_lp_tokens(asset1_balance, asset2_balance)
    f1 = fee_mult(tfee)
    f2 = fee_multHalf(tfee) / f1
    r = asset1_deposit / asset1_balance
    p = f2 * f2 + r / f1
    c = p.sqrt() - f2
    t = lpt_balance * (r - c) / (1 + c);
    return t

# Equation 4
def asset_in(asset1_balance: Decimal, asset2_balance: Decimal, lp_tokens: Decimal, tfee: Decimal) -> Decimal:
    lpt_balance = get_lp_tokens(asset1_balance, asset2_balance)
    f1 = fee_mult(tfee)
    f2 = fee_multHalf(tfee) / f1
    t1 = lp_tokens / lpt_balance
    t2 = 1 + t1
    d = f2 - t1 / t2
    a = 1 / (t2 * t2)
    b = 2 * d / t2 - 1 / f1
    c = d * d - f2 * f2
    return asset1_balance * solve_quadratic_eq(a, b, c)

# Equation 7
def lp_tokens_out(asset1_balance: Decimal, asset2_balance: Decimal, asset1_withdraw: Decimal, tfee: Decimal) -> Decimal:
    lpt_balance = get_lp_tokens(asset1_balance, asset2_balance)
    fr = asset1_withdraw / asset1_balance
    f1 = tfee
    c = fr * f1 + 2 - f1
    p = c * c - 4 * fr
    t = lpt_balance * (c - p.sqrt()) / 2
    return t

# Equation 8
def asset_out(asset1_balance: Decimal, asset2_balance: Decimal, lp_tokens: Decimal, tfee: Decimal) -> Decimal:
    lpt_balance = get_lp_tokens(asset1_balance, asset2_balance)
    f = tfee
    t1 = lp_tokens / lpt_balance
    b = asset1_balance * (t1 * t1 - t1 * (2 - f)) / (t1 * f - 1)
    return b

def to_decimal(n: str) -> Decimal:
    return Decimal(float(n))

i = 1
while i < len(sys.argv):
    # deposit by asset: asset1Balance,asset2Balance,assetDeposit,tfee
    if sys.argv[i] == '--d-asset':
        i += 1
        args = sys.argv[i].split(',')
        t = lp_tokens_in(to_decimal(args[0]), to_decimal(args[1]), to_decimal(args[2]), to_decimal(args[3]))
        print('deposit lptokens out', t)
    # deposit by lptokens: asset1Balance,asset2Balance,lpTokens,tfee
    elif sys.argv[i] == '--d-lptoken':
        i += 1
        args = sys.argv[i].split(',')
        t = asset_in(to_decimal(args[0]), to_decimal(args[1]), to_decimal(args[2]), to_decimal(args[3]))
        print('deposit asset out', t)
    # withdraw by asset: asset1Balance,asset2Balance,assetWithdraw,tfee
    elif sys.argv[i] == '--w-asset':
        i += 1
        args = sys.argv[i].split(',')
        t = lp_tokens_out(to_decimal(args[0]), to_decimal(args[1]), to_decimal(args[2]), to_decimal(args[3]))
        print('withdraw lptoken in', t)
    # withdraw by lptokens: asset1Balance,asset2Balance,lpTokens,tfee
    elif sys.argv[i] == '--w-lptoken':
        i += 1
        args = sys.argv[i].split(',')
        t = asset_out(to_decimal(args[0]), to_decimal(args[1]), to_decimal(args[2]), to_decimal(args[3]))
        print('withdraw asset in', t)
    i += 1
