import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { getMe, type Me } from './people'

/**
 * Who the app is talking to and what they may do (task `B-252`, ADR 0024), read once from
 * `/api/me`. The server refuses what a person may not do either way; this only decides what
 * to show. See docs/features/web-app.md#permissions.
 */

export interface MeState {
  /** Null until `/api/me` answers, and when it could not be read. */
  me: Me | null
  /** Whether `/api/me` has answered (or failed). */
  settled: boolean
  /**
   * Whether the person holds `permission`. True while unknown: hiding everything until the
   * answer arrives would flash an empty app, and the server still refuses.
   */
  can: (permission: string) => boolean
  reload: () => Promise<void>
}

const MeContext = createContext<MeState>({
  me: null,
  settled: false,
  can: () => true,
  reload: async () => undefined,
})

export function MeProvider({ children, load = getMe }: { children: ReactNode; load?: typeof getMe }) {
  const [me, setMe] = useState<Me | null>(null)
  const [settled, setSettled] = useState(false)

  const reload = useCallback(async () => {
    try {
      setMe(await load())
    } catch {
      setMe(null)
    } finally {
      setSettled(true)
    }
  }, [load])

  useEffect(() => {
    void reload()
  }, [reload])

  const value = useMemo<MeState>(
    () => ({
      me,
      settled,
      can: (permission) => me === null || me.permissions.includes(permission),
      reload,
    }),
    [me, settled, reload],
  )
  return <MeContext.Provider value={value}>{children}</MeContext.Provider>
}

/** The caller and a permission check. See {@link MeState}. */
export function useMe(): MeState {
  return useContext(MeContext)
}
