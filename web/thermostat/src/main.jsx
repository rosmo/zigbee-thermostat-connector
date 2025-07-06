// import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { Theme } from "@radix-ui/themes";

import './index.css'
import '@radix-ui/themes/styles.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <BrowserRouter>
    <Theme appearance="dark" accentColor="crimson" grayColor="sand" radius="large" scaling="95%">
      <App />
    </Theme>
  </BrowserRouter>,
)
