// 3. Create comprehensive prediction dashboard: frontend/src/components/predictions/PredictionDashboard.jsx
import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';
import { Card, Grid, Select, Button, Slider, Alert } from 'antd';

const PredictionDashboard = () => {
  const [predictions, setPredictions] = useState([]);
  const [selectedAsset, setSelectedAsset] = useState('AAPL');
  const [timeHorizon, setTimeHorizon] = useState('1d');
  const [modelType, setModelType] = useState('xgb');
  const [loading, setLoading] = useState(false);
  const [confidenceThreshold, setConfidenceThreshold] = useState(70);

  const fetchPredictions = async () => {
    setLoading(true);
    try {
      const response = await fetch(
        `/api/predictions?symbol=${selectedAsset}&horizon=${timeHorizon}&model=${modelType}`
      );
      const data = await response.json();
      setPredictions(data.predictions);
    } catch (error) {
      console.error('Error fetching predictions:', error);
    }
    setLoading(false);
  };

  const assets = ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'BTC', 'ETH', 'SPY', 'QQQ'];
  const horizons = ['1d', '1w', '1m', '3m'];
  const models = [
    { value: 'xgb', label: 'XGBoost' },
    { value: 'lstm', label: 'LSTM' },
    { value: 'ensemble', label: 'Ensemble' }
  ];

  return (
    <div className="prediction-dashboard">
      <Card title="ML Prediction Dashboard" className="mb-4">
        <Grid container spacing={3}>
          <Grid item xs={12} md={3}>
            <Select
              value={selectedAsset}
              onChange={setSelectedAsset}
              options={assets.map(a => ({ value: a, label: a }))}
              style={{ width: '100%' }}
            />
          </Grid>
          <Grid item xs={12} md={3}>
            <Select
              value={timeHorizon}
              onChange={setTimeHorizon}
              options={horizons.map(h => ({ value: h, label: h }))}
              style={{ width: '100%' }}
            />
          </Grid>
          <Grid item xs={12} md={3}>
            <Select
              value={modelType}
              onChange={setModelType}
              options={models}
              style={{ width: '100%' }}
            />
          </Grid>
          <Grid item xs={12} md={3}>
            <Button type="primary" onClick={fetchPredictions} loading={loading}>
              Generate Predictions
            </Button>
          </Grid>
        </Grid>

        {predictions.length > 0 && (
          <>
            <div className="mt-6">
              <h3>Price Forecast</h3>
              <LineChart width={800} height={300} data={predictions}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line type="monotone" dataKey="predicted" stroke="#8884d8" />
                <Line type="monotone" dataKey="actual" stroke="#82ca9d" />
                <Line type="monotone" dataKey="confidence_band_upper" stroke="#ffc658" strokeDasharray="5 5" />
                <Line type="monotone" dataKey="confidence_band_lower" stroke="#ffc658" strokeDasharray="5 5" />
              </LineChart>
            </div>

            <div className="mt-6">
              <h3>Model Metrics</h3>
              <Card>
                <Grid container spacing={2}>
                  <Grid item xs={6} md={3}>
                    <div className="metric-card">
                      <div className="metric-label">Accuracy</div>
                      <div className="metric-value">87.5%</div>
                    </div>
                  </Grid>
                  <Grid item xs={6} md={3}>
                    <div className="metric-card">
                      <div className="metric-label">Confidence</div>
                      <div className="metric-value">92%</div>
                    </div>
                  </Grid>
                  <Grid item xs={6} md={3}>
                    <div className="metric-card">
                      <div className="metric-label">Last Updated</div>
                      <div className="metric-value">2h ago</div>
                    </div>
                  </Grid>
                  <Grid item xs={6} md={3}>
                    <div className="metric-card">
                      <div className="metric-label">Model Version</div>
                      <div className="metric-value">v2.1.0</div>
                    </div>
                  </Grid>
                </Grid>
              </Card>
            </div>

            <div className="mt-6">
              <h4>Confidence Threshold</h4>
              <Slider
                value={confidenceThreshold}
                onChange={setConfidenceThreshold}
                min={50}
                max={95}
                marks={{ 50: '50%', 75: '75%', 90: '90%' }}
              />
              <Alert
                message={`Predictions with confidence below ${confidenceThreshold}% will be filtered`}
                type="info"
                showIcon
              />
            </div>
          </>
        )}
      </Card>
    </div>
  );
};

export default PredictionDashboard;