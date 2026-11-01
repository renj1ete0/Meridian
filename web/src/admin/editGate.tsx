import { createContext, useContext, type ReactNode } from 'react'

import { useMe } from '../lib/me'
import { BY_NAME_LABEL } from '../lib/permissionLabels'

/**
 * Controls that change something, disabled for a person who may view a section but not change
 * it (task `B-258`, ADR 0024). `AdminPage` names the permission the open section's changes need
 * (`SectionDef.edits`, the one its write routes require); every control that changes something
 * takes its props from {@link useEdit}, and a control that only reads is marked `data-view`.
 * The server refuses either way; this only stops a button that looks as if it works.
 * See docs/features/web-app.md#permissions.
 */

const EditPermission = createContext<string | null>(null)

/**
 * Names the permission changes need for everything inside it.
 *
 * @param permission - The section's `edits`; nothing (or null) leaves every control as it is.
 */
export function EditGate({ permission, children }: { permission?: string | null; children: ReactNode }) {
  return <EditPermission.Provider value={permission ?? null}>{children}</EditPermission.Provider>
}

/** The sentence a disabled control carries as its title: what it needs, by the permission's label. */
export function lockedReason(permission: string): string {
  return `Needs the ${BY_NAME_LABEL[permission] ?? permission} permission, which your access does not include.`
}

/** Props for one control that changes something; spread onto the element. */
export interface EditProps {
  disabled: boolean
  title: string | undefined
  /** Marks the control as one that changes something, for tests and screenshots. */
  'data-edit': ''
}

/**
 * Why changes are refused here, or null when they are not: for what is not a control, such as
 * a keyboard shortcut that decides.
 */
export function useEditLock(): string | null {
  const permission = useContext(EditPermission)
  const { can } = useMe()
  return permission !== null && !can(permission) ? lockedReason(permission) : null
}

/**
 * Props for the controls that change something, given the gate around them.
 *
 * @returns A function of the control's own reasons to be disabled (busy, nothing chosen) and its
 *   own title: locked, it is disabled and its title says which permission it needs instead.
 */
export function useEdit(): (disabled?: boolean, title?: string) => EditProps {
  const reason = useEditLock() ?? undefined
  return (disabled = false, title) => ({
    disabled: disabled || reason !== undefined,
    title: reason ?? title,
    'data-edit': '',
  })
}
