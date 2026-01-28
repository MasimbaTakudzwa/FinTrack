// frontend/src/components/predictions/SimplePrediction.jsx
import React, { useState } from 'react';
import axios from 'axios';

const SimplePrediction = () => {
  const [symbol, setSymbol] = useState('AAPL');
  const [prediction, setPrediction] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const symbols = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN', 'META', 'NVDA', 'BTC-USD'];

  const fetchPrediction = async () => {
    setLoading(true);
    setError('');
    
    try {
      // Call your Django backend which then calls ML service
      const response = await axios.post('/api/ml/predict/', {
        symbol: symbol,
        horizon: '1d'
      });
      
      setPrediction(response.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to get prediction');
      console.error('Prediction error:', err);
    }
    
    setLoading(false);
  };

  return (
    <div className="p-6 bg-white rounded-lg shadow-md">
      <h2 className="text-2xl font-bold mb-4">Market Predictions</h2>
      
      <div className="mb-6">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Select Asset
        </label>
        <select
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="w-full p-2 border border-gray-300 rounded-md"
        >
          {symbols.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <button
        onClick={fetchPrediction}
        disabled={loading}
        className="w-full bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 disabled:bg-blue-300"
      >
        {loading ? 'Predicting...' : 'Get Prediction'}
      </button>

      {error && (
        <div className="mt-4 p-3 bg-red-100 text-red-700 rounded-md">
          Error: {error}
        </div>
      )}

      {prediction && !error && (
        <div className="mt-6 p-4 bg-gray-50 rounded-md">
          <h3 className="text-lg font-semibold mb-2">Prediction Result</h3>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-sm text-gray-600">Symbol</p>
              <p className="font-medium">{prediction.symbol}</p>
            </div>
            
            <div>
              <p className="text-sm text-gray-600">Predicted Price</p>
              <p className="font-medium">
                ${prediction.predicted_price?.toFixed(2) || 'N/A'}
              </p>
            </div>
            
            <div>
              <p className="text-sm text-gray-600">Confidence</p>
              <p className="font-medium">
                {prediction.confidence ? `${(prediction.confidence * 100).toFixed(1)}%` : 'N/A'}
              </p>
            </div>
            
            <div>
              <p className="text-sm text-gray-600">Direction</p>
              <p className={`font-medium ${
                prediction.direction === 'up' ? 'text-green-600' : 
                prediction.direction === 'down' ? 'text-red-600' : 
                'text-gray-600'
              }`}>
                {prediction.direction ? prediction.direction.toUpperCase() : 'N/A'}
              </p>
            </div>
          </div>
          
          {prediction.recommendation && (
            <div className="mt-4 p-3 bg-blue-50 rounded-md">
              <p className="text-sm font-medium">Recommendation: {prediction.recommendation}</p>
            </div>
          )}
          
          <div className="mt-4 text-xs text-gray-500">
            <p>⚠️ Predictions are for informational purposes only. Past performance does not guarantee future results.</p>
          </div>
        </div>
      )}
    </div>
  );
};

export default SimplePrediction;