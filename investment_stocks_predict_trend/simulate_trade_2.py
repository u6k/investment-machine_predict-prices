import argparse
from datetime import datetime
import pandas as pd

from app_logging import get_app_logger
import app_s3
from simulate_trade_base import SimulateTradeBase


class SimulateTrade2(SimulateTradeBase):
    def simulate_impl(self, ticker_symbol, s3_bucket, input_base_path, output_base_path):
        L = get_app_logger(f"{self._job_name}.simulate_impl.{ticker_symbol}")
        L.info(f"{self._job_name}.simulate_impl: {ticker_symbol}")

        result = {
            "ticker_symbol": ticker_symbol,
            "exception": None
        }

        losscut_rate = 0.98
        take_profit_rate = 0.95
        minimum_profit_rate = 0.03

        try:
            # Load data
            df = app_s3.read_dataframe(s3_bucket, f"{input_base_path}/stock_prices.{ticker_symbol}.csv", index_col=0)

            # Simulate
            for start_id in df.index:
                losscut_price = df.at[start_id, "open_price"] * losscut_rate
                take_profit_price = df.at[start_id, "open_price"] * take_profit_rate
                sell_id = None
                sell_price = None
                take_profit = False

                for id in df.loc[start_id+1:].index:
                    # Sell: take profit
                    if take_profit:
                        sell_id = id
                        sell_price = df.at[id, "open_price"]
                        break

                    # Sell: losscut
                    if df.at[id, "low_price"] < losscut_price:
                        sell_id = id
                        sell_price = df.at[id, "low_price"]
                        break

                    # Flag take profit
                    if df.at[id, "high_price"] < take_profit_price:
                        take_profit = True

                    # Update losscut/take profit price
                    if losscut_price < (df.at[id, "close_price"] * losscut_rate):
                        losscut_price = df.at[id, "close_price"] * losscut_rate
                    if take_profit_price < (df.at[id, "high_price"] * take_profit_rate):
                        take_profit_price = df.at[id, "high_price"] * take_profit_rate

                # Set result
                if sell_id is not None:
                    df.at[start_id, "sell_id"] = sell_id
                    df.at[start_id, "buy_price"] = df.at[start_id, "open_price"]
                    df.at[start_id, "sell_price"] = sell_price
                    df.at[start_id, "profit"] = df.at[start_id, "sell_price"] - df.at[start_id, "buy_price"]
                    df.at[start_id, "profit_rate"] = df.at[start_id, "profit"] / df.at[start_id, "sell_price"]

            # Labeling for predict
            df["predict_target_value"] = df["profit_rate"].shift(-1)
            df["predict_target_label"] = df["predict_target_value"].apply(lambda r: 1 if r >= minimum_profit_rate else 0)

            # Save data
            app_s3.write_dataframe(df, s3_bucket, f"{output_base_path}/stock_prices.{ticker_symbol}.csv")
        except Exception as err:
            L.exception(f"ticker_symbol={ticker_symbol}, {err}")
            result["exception"] = err

        return result

    def forward_test_impl(self, ticker_symbol, s3_bucket, input_preprocess_base_path, input_simulate_base_path, input_model_base_path, output_base_path):
        L = get_app_logger(f"{self._job_name}.forward_test_impl.{ticker_symbol}")
        L.info(f"{self._job_name}.forward_test_impl: {ticker_symbol}")

        result = {
            "ticker_symbol": ticker_symbol,
            "exception": None
        }

        try:
            # Load data
            df_preprocess = app_s3.read_dataframe(s3_bucket, f"{input_preprocess_base_path}/stock_prices.{ticker_symbol}.csv", index_col=0)
            df = app_s3.read_dataframe(s3_bucket, f"{input_simulate_base_path}/stock_prices.{ticker_symbol}.csv", index_col=0)
            clf = app_s3.read_sklearn_model(s3_bucket, f"{input_model_base_path}/model.{ticker_symbol}.joblib")

            # Predict
            df_data = df_preprocess.drop("date", axis=1).dropna()
            predict = clf.predict(df_data.values)

            for i, id in enumerate(df_data.index):
                df.at[id, "predict"] = predict[i]

            # Test
            df["action"] = None

            for id in df.index[1:]:
                if df.at[id-1, "predict"] == 1:
                    df.at[id, "action"] = "trade"
                else:
                    df.at[id, "sell_id"] = None
                    df.at[id, "buy_price"] = None
                    df.at[id, "sell_price"] = None
                    df.at[id, "profit"] = None
                    df.at[id, "profit_rate"] = None

            # Save data
            app_s3.write_dataframe(df, s3_bucket, f"{output_base_path}/stock_prices.{ticker_symbol}.csv")
        except Exception as err:
            L.exception(f"ticker_symbol={ticker_symbol}, {err}")
            result["exception"] = err

        return result

    def forward_test_all(self, start_date, end_date, s3_bucket, base_path):
        L = get_app_logger(f"{self._job_name}.forward_test_all")
        L.info(f"{self._job_name}.forward_test_all: start")

        df_action = pd.DataFrame(columns=["date", "ticker_symbol", "action", "price", "stocks", "profit", "profit_rate"])
        df_stocks = pd.DataFrame(columns=["buy_price", "buy_stocks", "close_price_latest", "sell_id"])
        df_result = pd.DataFrame(columns=["fund", "asset"])

        df_report = app_s3.read_dataframe(s3_bucket, f"{base_path}/report.csv", index_col=0)

        df_prices_dict = {}
        for ticker_symbol in df_report.query("profit_factor>2.0").sort_values("expected_value", ascending=False).head(50).index:
            L.info(f"load data: {ticker_symbol}")
            df_prices_dict[ticker_symbol] = app_s3.read_dataframe(s3_bucket, f"{base_path}/stock_prices.{ticker_symbol}.csv", index_col=0)

        if len(df_prices_dict) == 0:
            for ticker_symbol in df_report.sort_values("expected_value", ascending=False).head(50).index:
                L.info(f"load data: {ticker_symbol}")
                df_prices_dict[ticker_symbol] = app_s3.read_dataframe(s3_bucket, f"{base_path}/stock_prices.{ticker_symbol}.csv", index_col=0)

        init_asset = 1000000
        fund = init_asset
        asset = init_asset
        available_rate = 0.05
        fee_rate = 0.001
        tax_rate = 0.21

        for date in self.date_range(start_date, end_date):
            date_str = date.strftime("%Y-%m-%d")
            L.info(f"test_all: {date_str}")

            # Buy
            for ticker_symbol in df_prices_dict.keys():
                if ticker_symbol in df_stocks.index:
                    continue

                df_prices = df_prices_dict[ticker_symbol]

                if len(df_prices.query(f"date=='{date_str}'")) == 0:
                    continue

                prices_id = df_prices.query(f"date=='{date_str}'").index[0]

                if df_prices.at[prices_id, "action"] != "buy":
                    continue

                buy_price = df_prices.at[prices_id, "open_price"]
                buy_stocks = init_asset * available_rate // buy_price

                if buy_stocks <= 0:
                    continue

                fee = (buy_price * buy_stocks) * fee_rate

                action_id = len(df_action)
                df_action.at[action_id, "date"] = date_str
                df_action.at[action_id, "ticker_symbol"] = ticker_symbol
                df_action.at[action_id, "action"] = "buy"
                df_action.at[action_id, "price"] = buy_price
                df_action.at[action_id, "stocks"] = buy_stocks
                df_action.at[action_id, "fee"] = fee

                df_stocks.at[ticker_symbol, "buy_price"] = buy_price
                df_stocks.at[ticker_symbol, "buy_stocks"] = buy_stocks
                df_stocks.at[ticker_symbol, "sell_id"] = df_prices.at[prices_id, "simulate_sell_id"]

                fund -= buy_price * buy_stocks + fee

            # Sell
            for ticker_symbol in df_stocks.index:
                df_prices = df_prices_dict[ticker_symbol]

                if len(df_prices.query(f"date=='{date_str}'")) == 0:
                    continue

                prices_id = df_prices.query(f"date=='{date_str}'").index[0]

                if df_stocks.at[ticker_symbol, "sell_id"] != prices_id:
                    continue

                sell_price = df_prices.at[prices_id, "sell_price"]
                buy_price = df_stocks.at[ticker_symbol, "buy_price"]
                buy_stocks = df_stocks.at[ticker_symbol, "buy_stocks"]
                profit = (sell_price - buy_price) * buy_stocks
                profit_rate = profit / (sell_price * buy_stocks)
                fee = sell_price * buy_stocks * fee_rate
                tax = profit * tax_rate if profit > 0 else 0

                action_id = len(df_action)
                df_action.at[action_id, "date"] = date_str
                df_action.at[action_id, "ticker_symbol"] = ticker_symbol
                df_action.at[action_id, "action"] = "sell"
                df_action.at[action_id, "price"] = sell_price
                df_action.at[action_id, "stocks"] = buy_stocks
                df_action.at[action_id, "profit"] = profit
                df_action.at[action_id, "profit_rate"] = profit_rate
                df_action.at[action_id, "fee"] = fee
                df_action.at[action_id, "tax"] = tax

                df_stocks = df_stocks.drop(ticker_symbol)

                fund += sell_price * buy_stocks - fee - tax

            # Update close_price_latest
            for ticker_symbol in df_stocks.index:
                df_prices = df_prices_dict[ticker_symbol]

                if len(df_prices.query(f"date=='{date_str}'")) == 0:
                    continue

                prices_id = df_prices.query(f"date=='{date_str}'").index[0]

                df_stocks.at[ticker_symbol, "close_price_latest"] = df_prices.at[prices_id, "close_price"]

            # Turn end
            asset = fund + (df_stocks["close_price_latest"] * df_stocks["buy_stocks"]).sum()

            df_result.at[date_str, "fund"] = fund
            df_result.at[date_str, "asset"] = asset

            L.info(df_result.loc[date_str])

        app_s3.write_dataframe(df_action, s3_bucket, f"{base_path}/test_all.action.csv")
        app_s3.write_dataframe(df_result, s3_bucket, f"{base_path}/test_all.result.csv")

        L.info("finish")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", help="simulate, or forward_test")
    parser.add_argument("--suffix", help="folder name suffix (default: test)", default="test")
    args = parser.parse_args()

    simulator = SimulateTrade2("simulate_trade_2")

    if args.task == "simulate":
        simulator.simulate(
            s3_bucket="u6k",
            input_base_path=f"ml-data/stocks/preprocess_1.{args.suffix}",
            output_base_path=f"ml-data/stocks/simulate_trade_2.{args.suffix}"
        )

        simulator.simulate_report(
            start_date="2018-01-01",
            end_date="2019-01-01",
            s3_bucket="u6k",
            base_path=f"ml-data/stocks/simulate_trade_2.{args.suffix}"
        )
    elif args.task == "forward_test":
        simulator.forward_test(
            s3_bucket="u6k",
            input_preprocess_base_path=f"ml-data/stocks/preprocess_3.{args.suffix}",
            input_simulate_base_path=f"ml-data/stocks/simulate_trade_2.{args.suffix}",
            input_model_base_path=f"ml-data/stocks/predict_3.simulate_trade_2.{args.suffix}",
            output_base_path=f"ml-data/stocks/forward_test_2.{args.suffix}"
        )

        simulator.forward_test_report(
            start_date="2018-01-01",
            end_date="2019-01-01",
            s3_bucket="u6k",
            base_path=f"ml-data/stocks/forward_test_2.{args.suffix}"
        )
    elif args.task == "forward_test_all":
        simulator.forward_test_all(
            start_date=datetime(2018, 1, 1),
            end_date=datetime(2019, 1, 1),
            s3_bucket="u6k",
            base_path=f"ml-data/stocks/forward_test_2.{args.suffix}"
        )
    else:
        parser.print_help()
