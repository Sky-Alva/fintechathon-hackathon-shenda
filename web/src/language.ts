import { createContext, useContext } from 'react'

/** The conversation's language, as the Agent decides it (docs/conversation-events.md):
 * Chinese by default, English when the Customer's latest message with any words in it is
 * in English (has Latin letters and no Chinese characters). The Channel's own controls
 * follow it. */
export type Language = 'zh' | 'en'

const HAN = /[㐀-䶿一-鿿豈-﫿]/
const LATIN = /[A-Za-z]/

/** The language after the Customer sends `text`: "500" or "1" on its own keeps it. */
export function customerLanguage(text: string, current: Language): Language {
  if (HAN.test(text)) return 'zh'
  if (LATIN.test(text)) return 'en'
  return current
}

/** One text of the Channel's own, in both languages. */
export type Texts = Record<Language, string>

export const LanguageContext = createContext<Language>('zh')

export function useLanguage(): Language {
  return useContext(LanguageContext)
}
