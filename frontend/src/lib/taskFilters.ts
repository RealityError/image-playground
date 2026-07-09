import type { TaskRecord } from '../types'

export function getFailedTaskIds(tasks: TaskRecord[]): string[] {
  return tasks.filter((task) => task.status === 'error').map((task) => task.id)
}
