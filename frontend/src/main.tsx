import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { UploadProvider } from './upload/UploadProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
      <UploadProvider>
          <App />
      </UploadProvider>
  </StrictMode>,
)
