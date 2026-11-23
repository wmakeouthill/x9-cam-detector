import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { installAuthFetchInterceptor } from '@/stores/authStore';
import './index.css';

installAuthFetchInterceptor();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
