import argparse
import joblib
import pandas as pd
from sklearn.preprocessing import minmax_scale

from app_logging import get_app_logger
import app_s3


def execute(s3_bucket, input_base_path, output_base_path):
    L = get_app_logger("preprocess_3")
    L.info("start")

    df_companies = app_s3.read_dataframe(s3_bucket, f"{input_base_path}/companies.csv", index_col=0)
    df_result = pd.DataFrame(columns=df_companies.columns)

    results = joblib.Parallel(n_jobs=-1)([joblib.delayed(preprocess)(ticker_symbol, s3_bucket, input_base_path, output_base_path) for ticker_symbol in df_companies.index])

    for result in results:
        if result["exception"] is not None:
            continue

        ticker_symbol = result["ticker_symbol"]
        df_result.loc[ticker_symbol] = df_companies.loc[ticker_symbol]

    app_s3.write_dataframe(df_result, s3_bucket, f"{output_base_path}/companies.csv")

    L.info("finish")


def preprocess(ticker_symbol, s3_bucket, input_base_path, output_base_path):
    L = get_app_logger(f"preprocess_3.{ticker_symbol}")
    L.info(f"preprocess_3: {ticker_symbol}")

    result = {
        "ticker_symbol": ticker_symbol,
        "exception": None
    }

    period = 20

    try:
        # Load data
        df = app_s3.read_dataframe(s3_bucket, f"{input_base_path}/stock_prices.{ticker_symbol}.csv", index_col=0)

        # Shift data
        columns = df.drop("date", axis=1).columns

        for column in columns:
            for i in range(period):
                df[f"{column}_{i}"] = minmax_scale(df[column].shift(i))
            df = df.drop(column, axis=1)

        # Save data
        app_s3.write_dataframe(df, s3_bucket, f"{output_base_path}/stock_prices.{ticker_symbol}.csv")

        # Convert array to image
        df = df.fillna(0.0).drop("date", axis=1)
        image_data = df.values.reshape(len(df), period, len(columns)) * 255.0

        # Save image
        app_s3.write_images(image_data, s3_bucket, f"{output_base_path}/stock_prices.{ticker_symbol}.png.tgz", f"stock_prices.{ticker_symbol}")
    except Exception as err:
        L.exception(f"ticker_symbol={ticker_symbol}, {err}")
        result["exception"] = err

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", help="folder name suffix (default: test)", default="test")
    args = parser.parse_args()

    execute(
        s3_bucket="u6k",
        input_base_path=f"ml-data/stocks/preprocess_2.{args.suffix}",
        output_base_path=f"ml-data/stocks/preprocess_3.{args.suffix}",
    )