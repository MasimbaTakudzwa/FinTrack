import { useParams } from "react-router-dom";
import { PagePlaceholder } from "./PagePlaceholder";

export function AssetDetail() {
  const { symbol } = useParams<{ symbol: string }>();
  return (
    <PagePlaceholder
      title={`Asset — ${symbol ?? ""}`}
      milestone="3D"
      description="OHLCV candlestick chart (TradingView Lightweight Charts) with price panel and recent news."
    />
  );
}
