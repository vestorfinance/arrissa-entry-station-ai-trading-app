import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import * as api from '../services/api.js'
import { useAuth } from './AuthContext.jsx'

// Shared chat-history state so the Sidebar (list) and Dashboard (active chat)
// stay in sync. `activeId` = the chat currently open (null = a fresh new chat).
const ChatsContext = createContext(null)

export function ChatsProvider({ children }) {
  const { token } = useAuth()
  const [chats, setChats] = useState([])
  const [activeId, setActiveId] = useState(null)

  const refresh = useCallback(async () => {
    if (!localStorage.getItem('auth_token')) return
    try { setChats(await api.listChats()) } catch { /* not logged in yet */ }
  }, [])

  // (Re)load history whenever the auth token changes — i.e. on login the history
  // populates immediately, and on logout it clears.
  useEffect(() => {
    if (token) refresh()
    else setChats([])
  }, [token, refresh])

  const newChat = useCallback(() => setActiveId(null), [])

  return (
    <ChatsContext.Provider value={{ chats, activeId, setActiveId, refresh, newChat }}>
      {children}
    </ChatsContext.Provider>
  )
}

export const useChats = () => useContext(ChatsContext)
