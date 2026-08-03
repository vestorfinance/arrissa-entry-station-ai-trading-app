import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import { ChatsProvider } from './context/ChatsContext.jsx'
import { initTheme } from './services/theme.js'
import './theme.css'

initTheme()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ChatsProvider>
          <App />
        </ChatsProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
