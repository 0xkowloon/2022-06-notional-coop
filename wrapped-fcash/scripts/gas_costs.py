import json
from brownie.network import Chain
from tests.helpers import get_lend_action
from brownie import Contract, wfCashERC4626, network, nUpgradeableBeacon, WrappedfCashFactory, accounts
from scripts.EnvironmentConfig import getEnvironment

chain = Chain()

def getEnv():
    name = network.show_active()
    if name == 'mainnet-fork':
        environment = getEnvironment('mainnet')
        environment.notional.upgradeTo('0x16eD130F7A6dcAc7e3B0617A7bafa4b470189962', {'from': environment.owner})
        environment.notional.updateAssetRate(1, "0x8E3D447eBE244db6D28E2303bCa86Ef3033CFAd6", {"from": environment.owner})
        environment.notional.updateAssetRate(2, "0x719993E82974f5b5eA0c5ebA25c260CD5AF78E00", {"from": environment.owner})
        environment.notional.updateAssetRate(3, "0x612741825ACedC6F88D8709319fe65bCB015C693", {"from": environment.owner})
        environment.notional.updateAssetRate(4, "0x39D9590721331B13C8e9A42941a2B961B513E69d", {"from": environment.owner})
        return environment
    elif name == 'kovan-fork':
        return getEnvironment('kovan')

def getFactory(env):
    impl = wfCashERC4626.deploy(env.notional.address, env.tokens['WETH'], {"from": env.deployer})
    beacon = nUpgradeableBeacon.deploy(impl.address, {"from": env.deployer})
    return WrappedfCashFactory.deploy(beacon.address, {"from": env.deployer})

def runAndLogGas(method, args, account):
    chain.snapshot()
    txn = method.transact(*args, {'from': account})
    chain.revert()
    return txn.gas_used

def main():
    env = getEnv()
    factory = getFactory(env)
    markets = env.notional.getActiveMarkets(2)
    gas = {}

    # Deploy Wrapper
    txn = factory.deployWrapper(2, markets[0][1])
    wrapper = Contract.from_abi("Wrapper", txn.events['WrapperDeployed']['wrapper'], wfCashERC4626.abi)
    gas["DeployWrapper"] = txn.gas_used

    lender = accounts[4]
    env.tokens["DAI"].transfer(lender, 1_000_000e18, {'from': env.whales["DAI_EOA"]})
    env.tokens["DAI"].approve(env.tokens['cDAI'].address, 2 ** 256 - 1, {'from': lender})
    env.tokens["cDAI"].mint(500_000e18, {'from': lender})
    env.tokens["DAI"].approve(wrapper.address, 2 ** 256 - 1, {'from': lender})
    env.tokens["cDAI"].approve(wrapper.address, 2 ** 256 - 1, {'from': lender})

    # Mint (via Underlying)
    gas["MintViaUnderlyingToken"] = runAndLogGas(wrapper.mintViaUnderlying, [ 100e18, 100e8, lender.address, 0.01e9 ], lender)
    # Mint (via Asset)
    gas["MintViaAssetToken"] = runAndLogGas(wrapper.mintViaAsset, [ 5000e8, 100e8, lender.address, 0.01e9], lender)
    gas["MintViaERC4626"] = runAndLogGas(wrapper.mint, [  100e8, lender.address ], lender)
    gas["DepositViaERC4626"] = runAndLogGas(wrapper.deposit, [ 100e18, lender.address ], lender)

    # Mint tokens for redeem tests
    wrapper.mint(100e8, lender.address, {"from": lender})
    env.tokens["cDAI"].approve(wrapper.address, 2 ** 256 - 1, {'from': lender})

    # Redeem via Transfer ERC1155
    gas["RedeemToERC1155"] = runAndLogGas(wrapper.redeem, [ 100e8, (False, True, lender.address, 0) ], lender)
    # Redeem pre Maturity (to Asset)
    gas["RedeemToAsset"] = runAndLogGas(wrapper.redeemToAsset, [ 100e8, lender.address, 0.1e9 ], lender)
    # Redeem pre Maturity (to Underlying)
    gas["RedeemToUnderlying"] = runAndLogGas(wrapper.redeemToUnderlying, [ 100e8, lender.address, 0.1e9 ], lender)
    # Redeem pre Maturity (ERC4626)
    gas["RedeemViaERC4626"] = runAndLogGas(wrapper.redeem, [ 100e8, lender.address, lender.address ], lender)
    # Withdraw pre Maturity (ERC4626)
    gas["WithdrawViaERC4626"] = runAndLogGas(wrapper.withdraw, [ 50e18, lender.address, lender.address ], lender)

    # Set Up ERC1155
    env.tokens["DAI"].approve(env.notional.address, 2 ** 256 - 1, {'from': lender})
    env.tokens["cDAI"].approve(env.notional.address, 2 ** 256 - 1, {'from': lender})
    action = get_lend_action(
        2,
        [{"tradeActionType": "Lend", "marketIndex": wrapper.getMarketIndex(), "notional": 100e8, "minSlippage": 0}],
        True,
    )
    lendCallData = env.notional.batchLend.encode_input(lender.address, [action])

    # Mint via ERC1155 Transfer
    env.notional.batchLend(lender.address, [action], {"from": lender})
    gas["MintViaERC1155Transfer"] = runAndLogGas(env.notional.safeTransferFrom,
        [lender.address, wrapper.address, wrapper.getfCashId(), 100e8, ""],
        lender
    )

    # Mint via ERC1155 Transfer w/ Batch Lend
    gas["MintViaERC1155TransferAndLendUnderlying"] = runAndLogGas(env.notional.safeTransferFrom,
        [lender.address, wrapper.address, wrapper.getfCashId(), 100e8, lendCallData],
        lender
    )

    action = get_lend_action(
        2,
        [{"tradeActionType": "Lend", "marketIndex": wrapper.getMarketIndex(), "notional": 100e8, "minSlippage": 0}],
        False,
    )
    lendCallData = env.notional.batchLend.encode_input(lender.address, [action])
    gas["MintViaERC1155TransferAndLendAsset"] = runAndLogGas(env.notional.safeTransferFrom,
        [lender.address, wrapper.address, wrapper.getfCashId(), 100e8, lendCallData],
        lender
    )

    chain.mine(1, timestamp=wrapper.getMaturity())
    env.notional.settleAccount(wrapper.address, {"from": lender})

    # Redeem post Maturity (to Asset)
    balance = wrapper.balanceOf(lender.address)
    gas["RedeemToAssetMatured"] = runAndLogGas(wrapper.redeemToAsset, [ balance, lender.address, 0.1e9 ], lender)
    # Redeem post Maturity (to Underlying)
    gas["RedeemToUnderlyingMatured"] = runAndLogGas(wrapper.redeemToUnderlying, [ balance, lender.address, 0.1e9 ], lender)
    # Withdraw post Maturity (ERC4626)
    gas["WithdrawViaERC4626Matured"] = runAndLogGas(wrapper.withdraw, [ 50e18, lender.address, lender.address ], lender)
    # Redeem post Maturity (ERC4626)
    gas["RedeemViaERC4626Matured"] = runAndLogGas(wrapper.redeem, [ balance, lender.address, lender.address ], lender)

    with open("gas_costs.json", "w") as f:
        json.dump(gas, f, indent=4, sort_keys=True)