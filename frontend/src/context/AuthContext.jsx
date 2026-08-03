import { createContext, useContext, useState, useCallback } from 'react'
import * as api from '../services/api.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('auth_token'))
  const [user, setUser] = useState(() => {
    const raw = localStorage.getItem('auth_user')
    return raw ? JSON.parse(raw) : null
  })

  const login = useCallback(async (email, password) => {
    const res = await api.login(email, password)
    setToken(res.token)
    setUser(res.user)
    localStorage.setItem('auth_token', res.token)
    localStorage.setItem('auth_user', JSON.stringify(res.user))
    return res
  }, [])

  // set the session directly from a {token, user} (e.g. after signup completes)
  const session = useCallback(({ token: tok, user: usr }) => {
    setToken(tok)
    setUser(usr)
    localStorage.setItem('auth_token', tok)
    localStorage.setItem('auth_user', JSON.stringify(usr))
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_user')
  }, [])

  const value = { token, user, isAuthed: !!token, login, session, logout }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within <AuthProvider>')
  return ctx
}
