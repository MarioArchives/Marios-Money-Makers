import { useState } from 'react'
import type { CompanyIconProps } from './CompanyIcon.props'
import './CompanyIcon.css'

const PLACEHOLDER_SRC = '/logos/_placeholder.svg'

export function CompanyIcon({ ticker, name, size = 32 }: CompanyIconProps): JSX.Element {
  const [src, setSrc] = useState(`/logos/${ticker}.svg`)

  return (
    <img
      className="company-icon"
      src={src}
      alt={name}
      width={size}
      height={size}
      onError={() => setSrc(PLACEHOLDER_SRC)}
    />
  )
}

export { PLACEHOLDER_SRC }
