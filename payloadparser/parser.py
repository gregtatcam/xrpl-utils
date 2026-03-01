#!/usr/bin/env python3
from collections import defaultdict
from xrpl.core import addresscodec
import hashlib
from xrpl.core.addresscodec import decode_classic_address
import json
import sys

from xrpl.models.requests.ledger_entry import Offer

# Json payloads file
payloads_file = None
# no env.close() after each transaction
no_close = False
# fee to use for transactions
fee = None

i = 1
while i < len(sys.argv):
    # payload is array or Json transaction payloads
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
# counter for unique names
unique_counter = 0

# get asset name from Json Amount or Asset object
# return XRP, Currency, or MPTTester instance
def get_asset(jv):
    if 'currency' in jv:
        return jv['currency']
    # it must have been created with MPTokenIssuanceCreate
    elif 'mpt_issuance_id' in jv:
        if not jv['mpt_issuance_id'] in MPT.mpts:
            raise Exception("unknown MPT issuance: " + jv['mpt_issuance_id'])
        return MPT.mpts[jv['mpt_issuance_id']]
    return "XRP"

def get_field(jv, field, default = None):
    return jv[field] if field in jv else (default if default is not None else None)

def get_asset_and_issuer(jv):
    asset = get_asset(jv)
    issuer = jv['issuer'] if 'issuer' in jv else None
    return [asset, issuer]

# get amount value from Json Amount
def get_value(jv):
    return jv['value'] if 'value' in jv else jv

# get amount string from Json Amount
# return XRP(value), account["Currency"](value), or MPTTester(value)
def get_amount(jv, field, fail = False, default = None):
    v = get_field(jv, field)
    if v is None:
        if fail:
            raise Exception("missing amount")
        return None if default is None else default
    [asset, issuer] = get_asset_and_issuer(v)
    value = get_value(v)
    if issuer is not None:
        issuer = get_account_name(issuer, True)
        return f"{issuer}[\"{asset}\"]({value})"
    return f"{asset}({value})"

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

# transaction fee
def get_tx_fee(jv):
    jv_fee = int(jv['Fee']) if 'Fee' in jv else 10
    return f"{jv_fee if fee is None else fee}"

# transaction
def do_cmd(tx, jv = None, close = True):
    if jv is not None:
        tx = add_tx_arg(tx, "txflags", get_field(jv, 'Flags'))
        tx = add_tx_arg(tx, "fee", get_tx_fee(jv))
    print(f"\tenv({tx});")
    if close:
        env_close()

# create account with given name and fund it with 100k XRP
def create_account(account, amount = None):
    global account_counter
    name = make_account(account)
    amount = amount if amount is not None else "XRP(\"100\'000\")"
    print(f"\tAccount const {name}(\"{name}\");")
    print(f"\tenv.fund({amount}, {name});")

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
    if arg is None:
        return args
    a = f".{name} = {arg}" if name is not (None or "") else f"{arg}"
    if args != "":
        args += f", \n\t\t{a}"
    else:
        args = a
    return args

def make_unique_var():
    global unique_counter
    name = f"var{unique_counter}"
    unique_counter += 1
    return name

def env_json(jv):
    var = make_unique_var()
    print(f"\tauto {var} = env.json({jv});")
    return var

def add_json(jv, field, value):
    if value is not None:
        print(f"\t{jv}[\"{field}\"] = {value};")

# add a comma-separated path to string
def add_path(path_str, path):
    if path_str == '':
        return path
    return path_str + ", " + path

# create a comma-separated list of paths from Json Paths array
def get_paths(jv):
    if jv is None:
        return None
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
                path_str = add_path(path_str, f"~{issuer}[\"{currency}\"]")
            elif 'mpt_issuance_id' in p:
                mpt = get_asset(p)
                path_str = add_path(path_str, f"~{mpt}")
        paths_str = add_path(paths_str, f"path({path_str})")
    return paths_str

"""
"AuthorizeCredentials": [{
    "Credential": {
      "Issuer": "ra5nK24KXen9AHvsdFTKHSANinZseWnPcX",
      "CredentialType": "6D795F63726564656E7469616C"
    }
  }],
"""
def get_credentials(jv, field):
    if field in jv is None:
        return None
    credentials_str = ""
    for credentials in jv[field]:
        credential = credentials['Credential']
        credential_str = f"{{{get_account_name(credential['Issuer'])}, {credential['CredentialType']}}}"
        credentials_str = add_arg(credentials_str, "", credential_str)
    credentials_str = f"{{{credentials_str}}}"
    return credentials_str

def get_credential_ids(jv):
    if 'CredetialIDs' not in jv:
        return None
    ids_str = ""
    for id in jv['CredetialIDs']:
        ids_str = add_arg(ids_str, "", id)
    return f"{{{ids_str}}}"

"""
"Permissions": [
        {
            "Permission": {
                "PermissionValue": "AccountDomainSet"
            }
        }
    ],
"""
def get_permissions(jv):
    if 'Permissions' not in jv:
        raise Exception("missing Permissions in payload")
    permissions_str = ""
    for permissions in jv['Permissions']:
        permission = permissions['Permission']
        permissions_str = add_arg(permissions_str, "", permission['PermissionValue'])
    permissions_str = f"{{{permissions_str}}}"
    return permissions_str

# add transaction argument
def add_tx_arg(args, name, arg):
    if arg is None:
        return args
    if name is not None:
        args += f",\n\t\t{name}({arg})"
    else:
        args += f",\n\t\t{arg}"
    return args

def cmd_from_arg(jv, cmd, arg, brackets = True):
    var = make_unique_var()
    if brackets:
        print(f"\tauto {var} = {cmd}({{{arg}}});")
    else:
        print(f"\tauto {var} = {cmd}({arg});")
    do_cmd(var, jv)


def make_sequence(account):
    var = make_unique_var()
    print(f"\tauto {var} = env.seq({account});")
    return var


class AMM:
    instances = defaultdict()
    def __init__(self, asset, asset2):
        self.asset = asset
        self.asset2 = asset2

    @staticmethod
    def get_AMM(jv, create = False):
        asset = get_asset(jv['Amount'])
        asset2 = get_asset(jv['Amount2'])
        fail_exists = create
        fail_not_exists = not create
        name = AMM.make_name(asset, asset2, fail_exists, fail_not_exists)
        if create:
            AMM.instances[name] = AMM(asset, asset2)
        return AMM.instances[name]

    @staticmethod
    def make_name(asset, asset2, fail_exists=False, fail_not_exists=False):
        name = f"{asset}_{asset2}" if asset > asset2 else f"{asset2}_{asset}"
        if fail_exists and name in AMM.instances:
            raise Exception("AMM already created: " + name)
        if fail_not_exists and name not in AMM.instances:
            raise Exception("AMM does not exist: " + name)
        return name

    def create(self, jv):
        arg = add_arg("", "", get_account_name(jv['Account']))
        arg = add_arg(arg, "", get_amount(jv, 'Amount'))
        arg = add_arg(arg, "", get_amount(jv, 'Amount2'))
        arg = add_arg(arg, "", get_field(jv, 'TradingFee', 0))
        cmd_from_arg(jv, "AMM::createJv", arg, False)

    def deposit(self, jv):
        arg = add_arg("", "account", get_account_name(jv['Account']))
        arg = add_arg(arg, "tokens", get_field(jv, 'LPTokenOut'))
        arg = add_arg(arg, "asset1In", get_amount(jv, 'Amount'))
        arg = add_arg(arg, "asset2In", get_amount(jv, 'Amount2'))
        arg = add_arg(arg, "maxEP", get_amount(jv, 'EPrice'))
        assets = f"{{{self.asset}, {self.asset2}}}"
        arg = add_arg(arg, "assets", assets)
        arg = add_arg(arg, "tfee", get_field(jv, 'TradingFee'))
        cmd_from_arg(jv, "AMM::depositJv", arg)

    def withdraw(self, jv):
        arg = add_arg("", "account", get_account_name(jv['Account']))
        arg = add_arg(arg, "tokens", get_field(jv, 'LPTokenIn'))
        arg = add_arg(arg, "asset1Out", get_amount(jv, 'Amount'))
        arg = add_arg(arg, "asset2Out", get_amount(jv, 'Amount2'))
        arg = add_arg(arg, "maxEP", get_amount(jv, 'EPrice'))
        assets = f"{{{self.asset}, {self.asset2}}}"
        arg = add_arg(arg, "assets", assets)
        arg = add_arg(arg, "tfee", get_field(jv, 'TradingFee'))
        cmd_from_arg(jv, "AMM::withdrawJv", arg)

    def clawback(self, jv):
        arg = add_arg("", "", get_account_name(jv['Account']))
        arg = add_arg(arg, "", get_account_name(jv['Holder']))
        arg = add_arg(arg, "", get_field(jv, 'Asset'))
        arg = add_arg(arg, "", get_field(jv, 'Asset2'))
        arg = add_arg(arg, "", get_amount(jv, 'Amount', default = "std::nullopt"))
        cmd_from_arg(jv, "amm::ammClawback", arg, True)

    def delete(self, jv):
        # TODO remove from instances
        arg = add_arg("", "", get_account_name(jv['Account']))
        arg = add_arg(arg, "", get_field(jv, 'Asset'))
        arg = add_arg(arg, "", get_field(jv, 'Asset2'))
        cmd_from_arg(jv, "AMM::deleteJv", arg)

    def vote(self, jv):
        arg = add_arg("", "account", get_account_name(jv['Account']))
        arg = add_arg(arg, "tfee", get_field(jv, 'TradingFee'))
        assets = f"{{{self.asset}, {self.asset2}}}"
        arg = add_arg(arg, "assets", assets)
        cmd_from_arg(jv, "AMM::voteJv", arg)

    @staticmethod
    def get_auth_accounts(jv):
        authAccounts = get_field(jv, 'AuthAccounts')
        if authAccounts is not None:
            jv = json.loads(authAccounts)
            authAccounts = "{"
            for authAccount in jv:
                authAccounts = add_arg(authAccounts, None, authAccount)
            authAccounts += "}"
        return authAccounts

    def bid(self, jv):
        arg = add_arg("", "account", get_account_name(jv['Account']))
        arg = add_arg(arg, "tokens", get_field(jv, 'LPTokenIn'))
        arg = add_arg(arg, "bidMin", get_amount(jv, 'BidMin'))
        arg = add_arg(arg, "bidMax", get_amount(jv, 'BidMax'))
        authAccounts = self.get_auth_accounts(jv)
        arg = add_arg(arg, "authAccounts", authAccounts)
        assets = f"{{{self.asset}, {self.asset2}}}"
        arg = add_arg(arg, "assets", assets)
        cmd_from_arg(jv, "AMM::bid", arg)

class Check:
    checks = defaultdict()
    instances = defaultdict()
    def __init__(self, check_id):
        self.check_id = check_id

    @staticmethod
    def calculate_check_id(account, sequence):
        # 1. Prefix for 'Check' space (0x0043)
        check_space_key = bytes.fromhex("0043")
        # 2. Convert an address to its 20-byte AccountID
        account_id = decode_classic_address(account)
        # 3. Convert sequence to 4-byte big-endian
        sequence_bytes = sequence.to_bytes(4, byteorder='big')
        # Concatenate and Hash
        data = check_space_key + account_id + sequence_bytes
        # SHA-512Half: Take the first 32 bytes (64 hex chars) of a SHA-512 hash
        check_id = hashlib.sha512(data).hexdigest()[:64].upper()
        return check_id

    @staticmethod
    def create_check_id(jv):
        account = jv['Account']
        sequence = jv['Sequence']
        var = make_unique_var()
        key = Check.calculate_check_id(account, sequence)
        print(f"uint256 const {var}(getCheckIndex({account}, env.seq({account})));")
        if key in Check.checks:
            raise Exception("check already created: " + jv)
        Check.checks[key] = var
        return var

    @staticmethod
    def get_check(jv, create=False):
        account = jv['Account']
        sequence = jv['Sequence']
        # check id from the payload
        key = Check.calculate_check_id(account, sequence)
        if create:
            if key in Check.instances:
                raise Exception("check already created: " + jv)
            # check id in the unit-test
            check_id = Check.create_check_id(jv)
            Check.checks[key] = Check(check_id)
        elif key not in Check.instances:
            raise Exception("check does not exist: " + key)
        return Check.instances[key]

    def create(self, jv):
        account = get_account_name(jv['Account'])
        destination = get_account_name(jv['Destination'])
        send_max = get_field(jv, 'Destinations')
        cmd = f"check::create({account}, {destination}, {send_max})"
        cmd = add_tx_arg(cmd, "dest_tag", get_field(jv, 'DestinationTag'))
        cmd = add_tx_arg(cmd, "expiration", get_field(jv, 'Expiration'))
        cmd = add_tx_arg(cmd, "invoice_id", get_field('InvoiceID'))
        do_cmd(cmd, jv)

    def cash(self, jv):
        account = get_account_name(jv['Account'])
        if 'Amount' in jv:
            cmd = f"check::cash({account}, {self.check_id}, {get_amount(jv, 'Amount')})"
        else:
            cmd = f"check::cash({account}, {self.check_id}, DeliverMin({get_amount(jv, 'DeliverMin')}))"
        do_cmd(cmd, jv)

    def cancel(self, jv):
        cmd = f"check::cancel({get_account_name(jv['Account'])}{self.check_id})"
        do_cmd(cmd, jv)

class Credential:
    def __init__(self):
        pass

    def create(self, jv):
        issuer = get_account_name(jv['Account'])
        subject = get_account_name(jv['Subject'])
        credential_type = get_field(jv, 'CredentialType')
        cmd = f"cred::create({subject}, {issuer}, {credential_type})"
        cmd = add_tx_arg(cmd, "credentials::uri", get_field(jv, 'URI'))
        var = env_json(cmd)
        add_json(var, "Expiration", get_field(jv, 'Expiration'))
        do_cmd(var, jv)

    def accept(self, jv):
        subject = get_account_name(jv['Account'])
        issuer = get_account_name(jv['Issuer'])
        credential_type = get_field(jv, 'CredentialType')
        cmd = f"cred::accept({subject}, {issuer}, {credential_type})"
        do_cmd(cmd, jv)

    def delete(self, jv):
        account = get_account_name(jv['Account'])
        credential_type = get_field(jv, 'CredentialType')
        subject = get_account_name(get_field(jv, 'Subject'))
        issuer = get_account_name(get_field(jv, 'Issuer'))
        subject = subject if subject is not None else account
        issuer = issuer if issuer is not None else account
        cmd = f"credentials::deleteCred({account}, {subject}, {issuer}, {credential_type})"
        do_cmd(cmd, jv)

class Escrow:
    escrows = defaultdict()
    instances = defaultdict()

    def __init__(self):
        self.sequence = None

    @staticmethod
    def get_escrow(jv, create=False):
        account = jv['Account']
        if create:
            sequence = jv['Sequence']
            key = f"{account}, {sequence}"
            if key in Escrow.escrows:
                raise Exception("escrow already created: " + jv)
            Escrow.instances[key] = Escrow()
        else:
            sequence = jv['OfferSequence']
            key = f"{account}, {sequence}"
            if key not in Escrow.escrows:
                raise Exception("escrow does not exist: " + key)
        return Escrow.instances[key]

    def create(self, jv):
        account = get_account_name(jv['Account'])
        destination = get_account_name(jv['Destination'])
        amount = get_amount(jv, 'Amount')
        sequence = make_sequence(account)
        self.sequence = sequence
        cmd = f"escrow::create({account}, {destination}, {amount})"
        cmd = add_tx_arg(cmd, "escrow::cancel_time", get_field(jv, 'CancelAfter'))
        cmd = add_tx_arg(cmd, "escrow::finish_time", get_field(jv, 'FinishAfter'))
        cmd = add_tx_arg(cmd, "escrow::condition", get_field(jv, 'Condition'))
        cmd = add_tx_arg(cmd, "dtag", get_field(jv, 'DestinationTag'))
        cmd = add_tx_arg(cmd, "escrow::fulfillment", get_field(jv, 'Fulfillment'))
        do_cmd(cmd, jv)

    def finish(self, jv):
        account = get_account_name(jv['Account'])
        owner = get_account_name(jv['Owner'])
        cmd = f"escrow::finish({account}, {owner}, {self.sequence})"
        cmd = add_tx_arg(cmd, "escrow::condition", get_field(jv, 'Condition'))
        cmd = add_tx_arg(cmd, "credentials::ids", get_credentials(jv))
        do_cmd(cmd, jv)

    def cancel(self, jv):
        account = get_account_name(jv['Account'])
        owner = get_account_name(jv['Owner'])
        cmd = f"escrow::cancel({account}, {owner}, {self.sequence})"
        do_cmd(cmd, jv)

class MPT:
    mpts = defaultdict()
    instances = defaultdict()
    def __init__(self, issuance_id):
        self.issuance_id = issuance_id
        self.test_issuance_id = f"{MPT.mpts[issuance_id]}.mpt()"

    @staticmethod
    def get_MPT(jv, create = False):
        if create:
            issuance_id = MPT.make_issuance_id(jv['Sequence'], jv['Account'])
        else:
            issuance_id = get_field(jv, 'MPTokenIssuanceID')
        if create:
            if issuance_id in MPT.mpts:
                raise Exception("MPT already created: " + issuance_id)
            MPT.create_mpt(jv)
            MPT.instances[issuance_id] = MPT(issuance_id)
        elif not issuance_id in MPT.mpts:
            raise Exception("MPT does not exist: " + issuance_id)
        return MPT.instances[issuance_id]

    @staticmethod
    def make_issuance_id(sequence, account):
        account_id_bytes = addresscodec.decode_classic_address(account)
        account_id_hex = account_id_bytes.hex().upper()
        issuance_id = f"{sequence:08x}{account_id_hex}"
        return issuance_id

    @staticmethod
    def create_mpt(jv):
        account = jv['Account']
        if not 'Sequence' in jv:
            raise Exception("missing Sequence in MPTTester payload")
        sequence = jv['Sequence']
        issuance_id = MPT.make_issuance_id(sequence, account)
        name = make_unique_var()
        account = get_account_name(account, True)
        # MPT(std::string const& n, xrpl::MPTID const& issuanceID_)
        print(f"\tauto {name} = MPT(\"\", makeMptID(env.seq({account}), {account}));")
        MPT.mpts[issuance_id] = name

    # create MPTTester instance given MPTokenIssuanceCreate payload
    def create(self, jv):
        arg = add_arg("", "issuer", get_account_name(jv['Account']))
        arg = add_arg(arg, "maxAmt", get_field(jv, 'MaximumAmount'))
        arg = add_arg(arg, "assetScale", get_field(jv, 'AssetScale'))
        arg = add_arg(arg, "transferFee", get_field(jv, 'TransferFee'))
        arg = add_arg(arg, "metadata", get_field(jv, 'MPTokenMetadata'))
        arg = add_arg(arg, "domainID", get_field(jv, 'DomainID'))
        cmd_from_arg(jv, "MPTTester::createJV", arg)

    def authorize(self, jv):
        arg = add_arg("", "account", get_account_name(jv['Account']))
        arg = add_arg(arg, "holder", get_account_name(get_field(jv, 'Holder')))
        arg = add_arg(arg, "id", self.test_issuance_id)
        cmd_from_arg(jv, "MPTTester::authorizeJV", arg)

    def set(self, jv):
        arg = add_arg("", "account", get_account_name(jv['Account']))
        arg = add_arg(arg, "holder", get_account_name(jv['Holder']))
        arg = add_arg(arg, "id", self.test_issuance_id)
        arg = add_arg(arg, "mutableFlags", get_field(jv, 'MutableFlags'))
        arg = add_arg(arg, "transferFee", get_field(jv, 'TransferFee'))
        arg = add_arg(arg, "metadata", get_field(jv, 'MPTokenMetadata'))
        arg = add_arg(arg, "delegate", get_field(jv, 'Delegate'))
        arg = add_arg(arg, "domainID", get_field(jv, 'DomainID'))
        cmd_from_arg(jv, "MPTTester::setJV", arg)

    def destroy(self, jv):
        # TODO remove from instances
        arg = add_arg("", "account", get_account_name(jv['Account']))
        arg = add_arg(arg, "id", self.test_issuance_id)
        cmd_from_arg(jv, "MPTTester::destroyJV", arg)

class Offer:
    # need mapping issuer, get, pays, sequence -> test sequence
    # in order to cancel the offer
    offers = defaultdict()
    instances = defaultdict()

    def __init__(self, jv):
        self.jv = jv
        self.sequence = None

    @staticmethod
    def get_offer(jv, create=False):
        account = jv['Account']
        taker_pays = jv['TakerPays']
        taker_gets = jv['TakerGets']
        sequence = jv['Sequence']
        key = f"{account}, {taker_pays}, {taker_gets}, {sequence}"
        if create:
            if key in Offer.offers:
                raise Exception("offer already created: " + jv)
            Offer.instances[key] = Offer(jv)
        elif key not in Offer.offers:
            raise Exception("offer does not exist: " + key)
        return Offer.instances[key]

    def create(self, jv):
        account = get_account_name(jv['Account'])
        taker_pays = get_amount(jv, 'TakerPays')
        taker_gets = get_amount(jv, 'TakerGets')
        self.sequence = make_sequence(account)
        cmd = f"offer({account}, {taker_pays}, {taker_gets})"
        do_cmd(cmd, jv)

    def cancel(self, jv):
        # TODO remove from instances
        account = get_account_name(jv['Account'], True)
        seq = self.sequence
        cmd = f"offer_cancel({account}, {seq})"
        do_cmd(cmd, jv)

def account_delete(jv):
    account = get_account_name(jv['Account'])
    dest = get_account_name(jv['Destination'])
    dest_tag = get_field(jv, 'DestinationTag')
    ids = get_credential_ids(jv)
    cmd = f"acctdelete({account}, {dest})"
    cmd = add_tx_arg(cmd, "dtag", dest_tag)
    cmd = add_tx_arg(cmd, "credentials::ids", ids)
    do_cmd(cmd, jv)

# currently supported SetFlag, ClearFlag, TransferRate, TickSize
def account_set(jv):
    account = get_account_name(jv['Account'])
    if 'SetFlag' in jv:
        flags = jv['SetFlag']
        do_cmd(f"fset({account}, {flags})")
    elif 'ClearFlag' in jv:
        flags = jv['ClearFlag']
        do_cmd(f"fclear({account}, {flags})")
    elif 'TransferRate' in jv:
        rate = int(jv['TransferRate'])
        do_cmd(f"rate({account}, {rate})")
    elif 'TickSize' in jv:
        tick_size = jv['TickSize']
        var = make_unique_var()
        print(f"\tauto {var} = noop({account});")
        print(f"\t{var}[sfTickSize.fieldName] = {tick_size};")
        do_cmd(var)
    else:
        raise Exception("unsupported account set : " + jv)


def delegate_set(jv):
    account = get_account_name(jv['Account'])
    authorize = get_account_name(jv['Authorize'])
    permissions = get_permissions(jv)
    do_cmd(f"delegate::set({account}, {authorize}, {permissions})")


def deposit_preauth(jv):
    account = get_account_name(jv['Account'])
    authorize = get_account_name(get_field(jv, 'Authorize'))
    authorize_credentials = get_credentials(jv, 'AuthorizeCredentials')
    unauthorize = get_account_name(get_field(jv, 'Unauthorize'))
    unauthorize_credentials = get_credentials(jv, 'UnauthorizeCredentials')
    if authorize is not None:
        cmd = f"deposit::auth({account}, {authorize})"
    elif unauthorize is not None:
        cmd = f"deposit::unauth({account}, {unauthorize})"
    elif authorize_credentials is not None:
        cmd = f"deposit::authCredentials({account}, {{{authorize_credentials}}})"
    elif unauthorize_credentials is not None:
        cmd = f"deposit::unauthCredentials({account}, {{{unauthorize_credentials}}})"
    do_cmd(cmd, jv)

# pay transaction from Json Payment payload
def pay(jv):
    account = get_account_name(jv['Account'], True)
    amount = get_amount(jv, 'Amount', True)
    dest = jv['Destination']
    # create an account
    if account == genesis:
        if dest in accounts:
            raise Exception("account already exists: " + dest)
        create_account(dest, amount)
        return
    dest = get_account_name(dest)
    cmd = f"pay({account}, {dest}, {amount})"
    cmd = add_tx_arg(cmd, "sendmax", get_amount(jv, 'SendMax'))
    cmd = add_tx_arg(cmd, "deliver_min", get_amount(jv, 'DeliverMin'))
    cmd = add_tx_arg(cmd, "domain", get_field(jv, 'DomainID'))
    cmd = add_tx_arg(cmd, "dest_tag", get_field(jv, 'DestinationTag'))
    cmd = add_tx_arg(cmd, None, get_paths(get_field(jv, 'Paths')))
    cmd = add_tx_arg(cmd, "credentials::ids", get_field(jv, 'CredentialIDs'))
    if 'DeliverMax' in jv:
        raise Exception("unsupported DeliverMax " + jv)
    do_cmd(cmd, jv)

# trustset transaction from Json TrustSet payload
def trustset(jv):
    account = get_account_name(jv['Account'])
    amount = get_amount(jv, 'LimitAmount')
    quality_in = jv['QualityIn'] if 'QualityIn' in jv else None
    quality_out = jv['QualityOut'] if 'QualityOut' in jv else None
    var = make_unique_var()
    cmd = f"trust({account}, {amount})"
    var = env_json(cmd)
    add_json(var, 'QualityIn', get_field(jv, 'QualityIn'))
    add_json(var, 'QualityOut', get_field(jv, 'QualityOut'))
    do_cmd(var, jv)

with open(payloads_file, "r") as f:
    payloads = json.load(f)
    print("\tEnv env(*this);")
    for p in payloads:
        account = p['Account']
        # one transaction at a time
        match p['TransactionType']:
            case "AccountDelete":
                account_delete(p)
            case "AccountSet":
                account_set(p)
            case "AMMCreate":
                amm = AMM.get_AMM(p, create = True)
                amm.create(p)
            case "AMMDeposit":
                amm = AMM.get_AMM(p)
                amm.deposit(p)
            case "AMMWithdraw":
                amm = AMM.get_AMM(p)
                amm.withdraw(p)
            case "AMMDelete":
                amm = AMM.get_AMM(p)
                amm.delete(p)
            case "AMMClawback":
                amm = AMM.get_AMM(p)
                amm.clawback(p)
            case "AMMVote":
                amm = AMM.get_AMM(p)
                amm.vote(p)
            case "AMMBid":
                amm = AMM.get_AMM(jv = p)
                amm.bid(p)
            case "CredentialAccept":
                cred = Credential()
                cred.accept(p)
            case "CredentialCreate":
                cred = Credential()
                cred.create(p)
            case "CredentialDelete":
                cred = Credential()
                cred.delete(p)
            case "DelegateSet":
                delegate_set(p)
            case "DepositPreauth":
                deposit_preauth(p)
            case "MPTokenIssuanceCreate":
                mpt = MPT.get_MPT(p, create = True)
                mpt.create(p)
            case "MPTokenAuthorize":
                mpt = MPT.get_MPT(p)
                mpt.authorize(p)
            case "MPTokenIssuanceSet":
                mpt = MPT.get_MPT(p)
                mpt.set(p)
            case "MPTokenIssuanceDestroy":
                mpt = MPT.get_MPT(p)
                mpt.destroy(p)
            case "OfferCreate":
                offer = Offer.get_offer(p, create = True)
                offer.create(p)
            case "OfferCancel":
                offer = Offer.get_offer(p)
                offer.cancel(p)
            case "Payment":
                pay(p)
            case "TrustSet":
                trustset(p)
            case _:
                raise Exception("unsupported tx type: " + p['TransactionType'])
