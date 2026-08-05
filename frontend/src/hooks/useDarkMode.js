import { useEffect, useState } from 'react'

import { useTheme } from '../contexts/ThemeContext'

function getSystemPreference() {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

export const useDarkMode = () => {
  const { theme } = useTheme()
  const [systemDark, setSystemDark] = useState(getSystemPreference)

  useEffect(() => {
    if (theme !== 'system') return

    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const handleMediaChange = (event) => {
      setSystemDark(event.matches)
    }

    setSystemDark(mediaQuery.matches)
    mediaQuery.addEventListener('change', handleMediaChange)

    return () => {
      mediaQuery.removeEventListener('change', handleMediaChange)
    }
  }, [theme])

  return theme === 'dark' || (theme === 'system' && systemDark)
}
