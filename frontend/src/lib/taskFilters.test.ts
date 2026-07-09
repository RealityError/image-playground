import { describe, expect, it } from 'vitest'
import type { TaskRecord } from '../types'
import { getFailedTaskIds } from './taskFilters'

const baseTask: TaskRecord = {
  id: 'base',
  prompt: '',
  params: { size: 'auto', quality: 'auto', n: 1 },
  inputImageIds: [],
  outputImages: [],
  status: 'done',
  error: null,
  createdAt: 1,
  finishedAt: 2,
  elapsed: 1,
}

describe('getFailedTaskIds', () => {
  it('只返回失败任务 id', () => {
    const tasks: TaskRecord[] = [
      { ...baseTask, id: 'done', status: 'done' },
      { ...baseTask, id: 'running', status: 'running', finishedAt: null },
      { ...baseTask, id: 'error-a', status: 'error', error: 'failed' },
      { ...baseTask, id: 'error-b', status: 'error', error: 'failed again' },
    ]

    expect(getFailedTaskIds(tasks)).toEqual(['error-a', 'error-b'])
  })
})
