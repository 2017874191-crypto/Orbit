import { homedir } from 'node:os'
import { join } from 'node:path'

export const getOrbitHome = (env: NodeJS.ProcessEnv = process.env) => env.ORBIT_HOME?.trim() || join(homedir(), '.orbit')

export const getOrbitHomeLabel = (env: NodeJS.ProcessEnv = process.env) => env.ORBIT_HOME?.trim() || '~/.orbit'
