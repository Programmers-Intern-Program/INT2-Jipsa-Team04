import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { UploadProvider } from './upload/UploadProvider'
import { SmartOrganizeProvider } from './smart/SmartOrganizeProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
      <UploadProvider>
          <SmartOrganizeProvider>
              <App />
          </SmartOrganizeProvider>
      </UploadProvider>
  </StrictMode>,
)
