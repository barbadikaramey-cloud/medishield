"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { loginApi, LoginResponse } from "./api";

export interface AuthUser {
  user_id: string;
  username: string;
  role: "CLAIMER" | "OPERATIONS_REVIEWER" | "ADMINISTRATOR" | "OPERATIONS_MANAGER" | string;
  token: string;
}

interface AuthContextType {
  user: AuthUser | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<AuthUser>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const TOKEN_KEY = "medishield_access_token";
const USER_KEY = "medishield_auth_user";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    try {
      const storedToken = localStorage.getItem(TOKEN_KEY);
      const storedUser = localStorage.getItem(USER_KEY);
      if (storedToken && storedUser) {
        const parsed = JSON.parse(storedUser);
        setUser({ ...parsed, token: storedToken });
      }
    } catch (err) {
      console.error("Failed to restore session", err);
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    } finally {
      setLoading(false);
    }
  }, []);

  const login = async (username: string, password: string): Promise<AuthUser> => {
    const res: LoginResponse = await loginApi(username, password);
    const authUser: AuthUser = {
      user_id: res.user_id,
      username: res.username,
      role: res.role,
      token: res.access_token,
    };
    localStorage.setItem(TOKEN_KEY, res.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify({
      user_id: res.user_id,
      username: res.username,
      role: res.role,
    }));
    setUser(authUser);
    return authUser;
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setUser(null);
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isAuthenticated: !!user,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

