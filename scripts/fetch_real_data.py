# scripts/fetch_real_data.py
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import psycopg2
import json
import os

def fetch_market_data(symbols, period='1y'):
    """Fetch real market data from Yahoo Finance"""
    all_data = {}
    
    for symbol in symbols:
        print(f"Fetching data for {symbol}...")
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period)
            
            if not df.empty:
                df.reset_index(inplace=True)
                df['symbol'] = symbol
                all_data[symbol] = df
                print(f"  ✓ Got {len(df)} days of data")
            else:
                print(f"  ✗ No data for {symbol}")
                
        except Exception as e:
            print(f"  ✗ Error fetching {symbol}: {str(e)}")
    
    return all_data

def save_to_database(data_dict, db_config):
    """Save data to PostgreSQL database"""
    conn = psycopg2.connect(**db_config)
    cursor = conn.cursor()
    
    for symbol, df in data_dict.items():
        for _, row in df.iterrows():
            cursor.execute("""
                INSERT INTO market_data (symbol, date, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """, (
                symbol, row['Date'], row['Open'], row['High'], 
                row['Low'], row['Close'], row['Volume']
            ))
    
    conn.commit()
    cursor.close()
    conn.close()
    print(f"Saved {sum(len(df) for df in data_dict.values())} records to database")

def create_feature_store():
    """Create feature store from database data"""
    from data_pipeline.processing.feature_engineer import FeatureEngineer
    import psycopg2
    
    # Connect to database
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        database=os.getenv('DB_NAME', 'fintrack'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', '')
    )
    
    # Fetch data
    query = """
    SELECT symbol, date, open, high, low, close, volume
    FROM market_data
    WHERE date >= CURRENT_DATE - INTERVAL '365 days'
    ORDER BY symbol, date
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Engineer features
    fe = FeatureEngineer()
    
    # Process each symbol separately
    all_features = []
    for symbol in df['symbol'].unique():
        symbol_data = df[df['symbol'] == symbol].copy()
        
        if len(symbol_data) > 30:  # Minimum data for features
            # Add features
            symbol_data = fe.add_technical_indicators(symbol_data)
            symbol_data = fe.add_time_features(symbol_data)
            symbol_data = fe.create_lag_features(symbol_data, ['close', 'volume'], [1, 2, 3, 5, 10])
            
            # Calculate target
            symbol_data['next_day_return'] = symbol_data['close'].pct_change().shift(-1)
            symbol_data['next_day_direction'] = (symbol_data['next_day_return'] > 0).astype(int)
            
            all_features.append(symbol_data)
    
    if all_features:
        features_df = pd.concat(all_features, ignore_index=True)
        features_df.to_csv('ml_models/data/feature_store.csv', index=False)
        print(f"Created feature store with {len(features_df)} records")
        return features_df
    else:
        print("No features created - insufficient data")
        return None

if __name__ == "__main__":
    # Symbols to fetch
    symbols = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN', 'META', 'NVDA', 'SPY']
    
    # Fetch data
    print("Fetching market data...")
    data = fetch_market_data(symbols)
    
    # Save to database if you have it set up
    # save_to_database(data, db_config)
    
    # Create feature store
    print("\nCreating feature store...")
    features_df = create_feature_store()
    
    if features_df is not None:
        print("\nFeature Store Statistics:")
        print(f"Total records: {len(features_df)}")
        print(f"Columns: {len(features_df.columns)}")
        print(f"Date range: {features_df['date'].min()} to {features_df['date'].max()}")
        print(f"Symbols: {features_df['symbol'].unique()}")
        
        # Save sample for inspection
        features_df.head(100).to_csv('ml_models/data/feature_sample.csv', index=False)
        print("\nSample saved to ml_models/data/feature_sample.csv")