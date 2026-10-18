import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import StaffConsole from './staff/StaffConsole.tsx'

// The Channel at /, the staff console at /staff.
const isStaff = window.location.pathname.replace(/\/+$/, '') === '/staff'

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isStaff ? <StaffConsole /> : <App />}</StrictMode>,
)
