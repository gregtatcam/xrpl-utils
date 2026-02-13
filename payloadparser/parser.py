#!/usr/bin/env python3
from collections import defaultdict
from xrpl.core import addresscodec
import json
import sys

# Json payloads file
payloads_file = None
# no env.close() after each transaction
no_close = False
# fee to use for transactions
fee = None

i = 1
while i < len(sys.argv):
    if sys.argv[i] == '--payload':
        i += 1
        payloads_file = sys.argv[i]
    elif sys.argv[i] == '--no-close':
        no_close = True
    elif sys.argv[i] == '--fee':
        i += 1
        fee = int(sys.argv[i])
    else:
        ex = f"unknown argument: {sys.argv[i]}"
        raise Exception(ex)
    i += 1

if payloads_file is None:
    print('payloads file must be provided')
    exit(0)

# supported tx types
supported_tx_types = ["AccountSet", "AMMCreate", "MPTokenAuthorize",
                      "MPTokenIssuanceCreate", "OfferCreate", "Payment"]

# genesis account
genesis = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
# AccountID to account name
accounts = defaultdict()
# counter for account names
account_counter = 0
# counter for MPT issuances
mpts_counter = 0
# AMM pair to AMM instance
amms = defaultdict()
# MPT ID to MPTTester instance
mpts = defaultdict()
nots = defaultdict()

# get asset name from Json Amount or Asset object
# return XRP, Currency, or MPTTester instance
def get_asset(jv):
    if 'currency' in jv:
        return jv['currency']
    # it must have been created with MPTokenIssuanceCreate
    elif 'mpt_issuance_id' in jv:
        if not jv['mpt_issuance_id'] in mpts:
            raise Exception("unknown MPT issuance: " + jv['mpt_issuance_id'])
        return mpts[jv['mpt_issuance_id']]
    return "XRP"

# get amount value from Json Amount
def get_value(jv):
    return jv['value'] if 'value' in jv else jv

# get amount string from Json Amount
# return XRP(value), Currency(value), or MPTTester(value)
def get_amount(jv, fail = False):
    if jv is None:
        if fail:
            raise Exception("missing amount")
        return None
    asset = get_asset(jv)
    value = get_value(jv)
    return f"{asset}({value})"

# make amm name from asset and asset2
def make_amm_name(asset, asset2):
    return f"{asset}_{asset2}" if asset > asset2 else f"{asset2}_{asset}"

# make account name from account
# store AccountID to account name in accounts
# return account name
def make_account(account):
    global account_counter
    global accounts
    name = 'account' + str(account_counter)
    accounts[account] = name
    account_counter += 1
    return name

# env close
def env_close():
    if not no_close:
        print("\tenv.close();")

# transaction
def do_cmd(tx, close = True):
    print(tx)
    if close:
        env_close()

# transaction fee
def get_tx_fee(jv):
    jv_fee = int(jv['Fee']) if 'Fee' in jv else 10
    return f", fee({jv_fee if fee is None else fee})"

# create account with given name and fund it with 100k XRP
def create_account(account, amount = None):
    global account_counter
    name = make_account(account)
    amount = amount if amount is not None else "XRP(\"100\'000\")"
    print(f"\tAccount const {name}(\"{name}\");")
    do_cmd(f"\tenv.fund({amount}, {name});")

# get account name from account ID or create it
def get_account_name(account, fail = False):
    global accounts
    if account == genesis:
        return account
    if account is None:
        return account
    if not account in accounts:
        if fail:
            raise Exception("unknown account: " + account)
        # assume it should be created
        create_account(account)
    return accounts[account]

# add comma-separated argument to string
def add_arg(args, name, arg):
    a = f".{name} = {arg}"
    if args != "":
        args += f", {a}"
    else:
        args = a
    return args

# make MPTTester name from sequence and account ID
# return name and issuance ID
def make_mpttester_name(sequence, account):
    global mpts_counter
    account_id_bytes = addresscodec.decode_classic_address(account)
    account_id_hex = account_id_bytes.hex().upper()
    issuance_id = f"{sequence:08x}{account_id_hex}"
    name = f"MPT{mpts_counter}"
    mpts[issuance_id] = name
    mpts_counter += 1
    return [name, issuance_id]

# create MPTTester instance given MPTokenIssuanceCreate payload
def create_mptoken_issuance(jv):
    global mpts_counter
    # transaction must have Sequence
    if not 'Sequence' in jv:
        raise Exception("missing Sequence")
    sequence = int(jv['Sequence'])
    max_amount = jv['MaximumAmount'] if 'MaximumAmount' in jv else None
    flags = jv['Flags'] if 'Flags' in jv else None
    transfer_fee = jv['TransferFee'] if 'TransferFee' in jv else None
    asset_scale = jv['AssetScale'] if 'AssetScale' in jv else None
    domain_id = jv['DomainID'] if 'DomainID' in jv else None
    metadata = jv['MPTokenMetadata'] if 'MPTokenMetadata' in jv else None
    account = jv['Account']
    [name, issuance_id] = make_mpttester_name(sequence, account)
    account = get_account_name(account)
    create_arg = ""
    if max_amount is not None:
        create_arg = add_arg("", "maxAmt", max_amount)
    if asset_scale is not None:
        create_arg = add_arg(create_arg, "assetScale", asset_scale)
    if transfer_fee is not None:
        create_arg = add_arg(create_arg, "transferFee", transfer_fee)
    if metadata is not None:
        create_arg = add_arg(create_arg, "metadata", f"\"{metadata}\"")
    if flags is not None:
        create_arg = add_arg(create_arg, "flags", flags)
    if domain_id is not None:
        create_arg = add_arg(create_arg, "domainID", domain_id)
    cmd = f"\tMPTTester {name}(env, {account}, MPTInit{{.fund = false, .create = MPTCreate{{{create_arg}}}}});"
    do_cmd(cmd, False)

# authorize MPToken instance given MPTokenAuthorize payload
def authorize_mptoken(jv):
    account = get_account_name(jv['Account'])
    issuance_id = jv['MPTokenIssuanceID']
    if not issuance_id in mpts:
        raise Exception("unknown MPT issuance ID: " + issuance_id)
    mpt = mpts[issuance_id]
    flags = jv['Flags'] if 'Flags' in jv else None
    holder = get_account_name(jv['Holder'] if 'Holder' in jv else None)
    authorize_arg = f".account = {account}"
    if holder is not None:
        authorize_arg = add_arg(authorize_arg, "holder", holder)
    if flags is not None and flags != 0:
        authorize_arg = add_arg(authorize_arg, "flags", flags)
    cmd = f"\t{mpt}.authorize(MPTAuthorize{{{authorize_arg}}});"
    do_cmd(cmd, False)

# add a comma-separated path to string
def add_path(path_str, path):
    if path_str == '':
        return path
    return path_str + ", " + path

# create a comma-separated list of paths from Json Paths array
def get_paths(jv):
    if jv == '':
        raise Exception("empty paths")
    paths_str = ""
    for path in jv:
        path_str = ""
        for p in path:
            if 'issuer' in p and not 'currency' in p:
                issuer = get_account_name(p['issuer'], True)
                path_str = add_path(path_str, f"{issuer}")
            if 'currency' in p and 'issuer' in p:
                currency = get_asset(p)
                issuer = get_account_name(p['issuer'], True)
                path_str = add_path(path_str, f"~{issuer}[\"{currency}\"])")
            elif 'mpt_issuance_id' in p:
                mpt = get_asset(p)
                path_str = add_path(path_str, f"~{mpt}")
        paths_str = add_path(paths_str, f"path({path_str})")
    return paths_str

def add_tx_arg(args, name, arg):
    if name is not None:
        args += f",\n\t\t{name}({arg})"
    else:
        args += f",\n\t\t({arg})"
    return args

# pay transaction from Json Payment payload
def pay(jv):
    account = get_account_name(jv['Account'], True)
    amount = get_amount(jv['Amount'], True)
    dest = jv['Destination']
    # create an account
    if account == genesis:
        if dest in accounts:
            raise Exception("account already exists: " + dest)
        create_account(dest, amount)
        return
    dest = get_account_name(dest)
    pay_cmd = f"pay({account}, {dest}, {amount})"
    if 'Flags' in jv:
        pay_cmd = add_tx_arg(pay_cmd, "txflags", jv['Flags'])
    if 'SendMax' in jv:
        pay_cmd = add_tx_arg(pay_cmd, "sendmax", get_amount(jv['SendMax']))
    if 'DeliverMin' in jv:
        pay_cmd = add_tx_arg(pay_cmd, "deliver_min", get_amount(jv['DeliverMin']))
    if 'DomainID' in jv:
        pay_cmd = add_tx_arg("pay_cmd, domain", jv['DomainID'])
    if 'DestinationTag' in jv:
        pay_cmd = add_tx_arg(pay_cmd, "dest_tag", jv['DestinationTag'])
    if 'DeliverMax' in jv:
        raise Exception("unsupported DeliverMax " + jv)
    if 'Paths' in jv:
        pay_cmd = add_tx_arg(pay_cmd, None, get_paths(jv['Paths']))
    if 'CredentialIDs' in jv:
        raise Exception("unsupported CredentialIDs " + jv)
    pay_cmd = f"\tenv({pay_cmd});"
    do_cmd(pay_cmd)

# currently supported SetFlag, ClearFlag, TransferRate, TickSize
def account_set(jv):
    account = get_account_name(jv['Account'])
    if 'SetFlag' in jv:
        flags = jv['SetFlag']
        do_cmd(f"\tenv(fset({account}, {flags}));")
    elif 'ClearFlag' in jv:
        flags = jv['ClearFlag']
        do_cmd(f"\tenv(fclear({account}, {flags}));")
    elif 'TransferRate' in jv:
        rate = float(jv['TransferRate'])/1_000_000_000
        do_cmd(f"env(rate({account}, {rate}));")
    elif 'TickSize' in jv:
        tick_size = jv['TickSize']
        print(f"\t{{")
        print(f"\t\tauto txn = noop({account});")
        print(f"\t\ttxn[sfTickSize.fieldName] = {tick_size};")
        print("\t\tenv(txn);")
        print("\t\tenv.close();")
        print(f"}}")
    else:
        raise Exception("unsupported account set : " + jv)

# create AMM instance from AMMCreate payload
def create_amm(jv):
    account = get_account_name(jv['Account'])
    amount = get_amount(jv['Amount'])
    amount2 = get_amount(jv['Amount2'])
    name = make_amm_name(get_asset(jv['Amount']), get_asset(jv['Amount2']))
    if name in amms:
        raise Exception("AMM already created: " + name)
    amms[name] = True
    trading_fee = jv['TradingFee'] if 'TradingFee' in jv else 0
    if trading_fee is not None:
        trading_fee = f", false, {trading_fee}"
    do_cmd(f"\tAMM {name}(env, {account}, {amount}, {amount2}{trading_fee});")

with open(payloads_file, "r") as f:
    payloads = json.load(f)
    print("\tEnv env(*this);")
    for p in payloads:
        account = p['Account']
        # one transaction at a time
        match p['TransactionType']:
            case "AccountSet":
                account_set(p)
            case "AMMCreate":
                create_amm(p)
            case "MPTokenIssuanceCreate":
                create_mptoken_issuance(p)
            case "MPTokenAuthorize":
                authorize_mptoken(p)
            case "OfferCreate":
                nots[""] = ""
            case "Payment":
                pay(p)
            case _:
                raise Exception("unsupported tx type: " + p['TransactionType'])
