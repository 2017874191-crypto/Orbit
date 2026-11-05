import { homedir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { getOrbitHome, getOrbitHomeLabel } from '../config/paths.js'

describe('Orbit product paths', () => {
  it('uses the Orbit home override for persistent TUI state', () => {
    expect(getOrbitHome({ ORBIT_HOME: '/tmp/orbit-home' })).toBe('/tmp/orbit-home')
    expect(getOrbitHomeLabel({ ORBIT_HOME: '/tmp/orbit-home' })).toBe('/tmp/orbit-home')
  })

  it('uses ~/.orbit when the override is empty', () => {
    expect(getOrbitHome({ ORBIT_HOME: '  ' })).toBe(join(homedir(), '.orbit'))
    expect(getOrbitHomeLabel({ ORBIT_HOME: '  ' })).toBe('~/.orbit')
  })
})
