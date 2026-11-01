/** The words on every stub's label (`B-256`), so each surface says the same thing. */
export const PARTIAL_LABEL = 'first paragraph only — paywalled'

/**
 * The mark on a stub (`B-256`): only a paywalled page's headline and visible opening were
 * kept, so what is shown is not the article. Links to the original, where a reader with
 * access can read the rest. See docs/features/web-app.md#stubs.
 *
 * @param url - The page the stub was kept from.
 */
export function PartialLabel({ url }: { url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      title="Only the headline and the opening the page shows everyone were kept. This is not the article."
      className="inline-flex items-center border border-accent-attention/50 bg-accent-attention/10 px-1.5 py-0.5 font-mono text-[10px] text-accent-attention hover:underline"
    >
      {PARTIAL_LABEL} ↗
    </a>
  )
}
