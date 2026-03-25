// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IPoolAddressesProvider {
    function getPool() external view returns (address);
}

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}

interface ISwapRouterV3 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24  fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external returns (uint256 amountOut);

    struct ExactInputParams {
        bytes   path;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
    }
    function exactInput(ExactInputParams calldata params)
        external returns (uint256 amountOut);
}

interface IAerodromeRouter {
    struct Route {
        address from;
        address to;
        bool    stable;
        address factory;
    }
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        Route[] calldata routes,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract FlashLoanArbitrage is IFlashLoanSimpleReceiver {

    address public constant AAVE_POOL_ADDRESSES_PROVIDER =
        0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address public constant UNISWAP_V3_ROUTER =
        0x2626664c2603336E57B271c5C0b26F421741e481;
    address public constant AERODROME_ROUTER =
        0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43;
    address public constant AERODROME_DEFAULT_FACTORY =
        0x420DD381b31aEf6683db6B902084cB0FFECe40Da;

    address public constant USDC  = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address public constant WETH  = 0x4200000000000000000000000000000000000006;
    address public constant DAI   = 0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb;
    address public constant USDT  = 0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2;
    address public constant cbETH = 0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22;

    address public owner;
    IPool   public aavePool;

    struct SwapStep {
        address tokenIn;
        address tokenOut;
        uint8   dex;
        uint24  uniV3Fee;
        bool    aeroStable;
    }

    struct ArbParams {
        uint8      hops;
        uint256    minProfit;
        SwapStep[] steps;
    }

    event FlashLoanExecuted(
        address indexed tokenBorrow,
        uint256 borrowed,
        uint256 profit,
        uint8   hops
    );
    event EmergencyWithdraw(address token, uint256 amount);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
    modifier onlyPool() {
        require(msg.sender == address(aavePool), "Caller not Aave Pool");
        _;
    }

    constructor() {
        owner = msg.sender;
        aavePool = IPool(
            IPoolAddressesProvider(AAVE_POOL_ADDRESSES_PROVIDER).getPool()
        );
    }

    function initiateFlashLoan(
        address token,
        uint256 amount,
        bytes calldata arbParams
    ) external onlyOwner {
        aavePool.flashLoanSimple(
            address(this), token, amount, arbParams, 0
        );
    }

    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override onlyPool returns (bool) {

        require(initiator == address(this), "Invalid initiator");

        ArbParams memory arb = abi.decode(params, (ArbParams));
        require(arb.steps.length == arb.hops, "Steps/hops mismatch");

        uint256 repayAmount = amount + premium;

        uint256 currentAmount = amount;
        for (uint256 i = 0; i < arb.steps.length; i++) {
            SwapStep memory step = arb.steps[i];
            if (step.dex == 0) {
                currentAmount = _swapUniV3(
                    step.tokenIn, step.tokenOut, step.uniV3Fee, currentAmount
                );
            } else {
                currentAmount = _swapAerodrome(
                    step.tokenIn, step.tokenOut, currentAmount, step.aeroStable
                );
            }
        }

        uint256 finalBalance = IERC20(asset).balanceOf(address(this));

        require(
            finalBalance >= repayAmount + arb.minProfit,
            "Profit below minimum"
        );

        uint256 profit = finalBalance - repayAmount;

        IERC20(asset).approve(address(aavePool), repayAmount);

        emit FlashLoanExecuted(asset, amount, profit, arb.hops);
        return true;
    }

    function _swapUniV3(
        address tokenIn,
        address tokenOut,
        uint24  fee,
        uint256 amountIn
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).approve(UNISWAP_V3_ROUTER, amountIn);
        amountOut = ISwapRouterV3(UNISWAP_V3_ROUTER).exactInputSingle(
            ISwapRouterV3.ExactInputSingleParams({
                tokenIn:           tokenIn,
                tokenOut:          tokenOut,
                fee:               fee,
                recipient:         address(this),
                deadline:          block.timestamp + 60,
                amountIn:          amountIn,
                amountOutMinimum:  0,
                sqrtPriceLimitX96: 0
            })
        );
    }

    function _swapUniV3MultiHop(
        bytes memory path,
        address tokenIn,
        uint256 amountIn
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).approve(UNISWAP_V3_ROUTER, amountIn);
        amountOut = ISwapRouterV3(UNISWAP_V3_ROUTER).exactInput(
            ISwapRouterV3.ExactInputParams({
                path:             path,
                recipient:        address(this),
                deadline:         block.timestamp + 60,
                amountIn:         amountIn,
                amountOutMinimum: 0
            })
        );
    }

    function _swapAerodrome(
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        bool    stable
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).approve(AERODROME_ROUTER, amountIn);
        IAerodromeRouter.Route[] memory routes = new IAerodromeRouter.Route[](1);
        routes[0] = IAerodromeRouter.Route({
            from:    tokenIn,
            to:      tokenOut,
            stable:  stable,
            factory: AERODROME_DEFAULT_FACTORY
        });
        uint256[] memory amounts = IAerodromeRouter(AERODROME_ROUTER)
            .swapExactTokensForTokens(
                amountIn, 0, routes, address(this), block.timestamp + 60
            );
        amountOut = amounts[amounts.length - 1];
    }

    function _swapAerodromeMultiHop(
        address tokenIn,
        address tokenMid,
        address tokenOut,
        uint256 amountIn,
        bool    stable1,
        bool    stable2
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).approve(AERODROME_ROUTER, amountIn);
        IAerodromeRouter.Route[] memory routes = new IAerodromeRouter.Route[](2);
        routes[0] = IAerodromeRouter.Route({
            from: tokenIn, to: tokenMid,
            stable: stable1, factory: AERODROME_DEFAULT_FACTORY
        });
        routes[1] = IAerodromeRouter.Route({
            from: tokenMid, to: tokenOut,
            stable: stable2, factory: AERODROME_DEFAULT_FACTORY
        });
        uint256[] memory amounts = IAerodromeRouter(AERODROME_ROUTER)
            .swapExactTokensForTokens(
                amountIn, 0, routes, address(this), block.timestamp + 60
            );
        amountOut = amounts[amounts.length - 1];
    }

    function withdrawToken(address token) external onlyOwner {
        uint256 balance = IERC20(token).balanceOf(address(this));
        require(balance > 0, "Nothing to withdraw");
        IERC20(token).transfer(owner, balance);
        emit EmergencyWithdraw(token, balance);
    }

    function withdrawETH() external onlyOwner {
        uint256 bal = address(this).balance;
        require(bal > 0, "Nothing to withdraw");
        payable(owner).transfer(bal);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        owner = newOwner;
    }

    receive() external payable {}
}
