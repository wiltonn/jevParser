import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { api } from "./api";
import type { Job, User } from "./types";

export function useApi<T>(key: QueryKey, path: string | null, opts: { refetchInterval?: number | false | ((data: T | undefined) => number | false) } = {}) {
  return useQuery<T>({
    queryKey: key,
    queryFn: () => api.get<T>(path!),
    enabled: path !== null,
    refetchInterval: opts.refetchInterval
      ? (q) => (typeof opts.refetchInterval === "function"
          ? (opts.refetchInterval as (d: T | undefined) => number | false)(q.state.data as T | undefined)
          : (opts.refetchInterval as number))
      : undefined,
  });
}

export function useMe() {
  return useApi<User>(["me"], "/me");
}

const ACTIVE = new Set(["queued", "running"]);
export const isActive = (status?: string | null) => !!status && ACTIVE.has(status);

/** Poll a job once a second until it settles, then invalidate ``onDone`` keys. */
export function useJob(jobId: number | null | undefined, onDone: QueryKey[] = []) {
  const qc = useQueryClient();
  return useQuery<Job>({
    queryKey: ["job", jobId],
    queryFn: async () => {
      const job = await api.get<Job>(`/jobs/${jobId}`);
      if (!isActive(job.status)) onDone.forEach((k) => qc.invalidateQueries({ queryKey: k }));
      return job;
    },
    enabled: !!jobId,
    refetchInterval: (q) => (isActive(q.state.data?.status) || !q.state.data ? 1000 : false),
  });
}

export function useAction<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>, invalidate: QueryKey[] = []) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k })),
  });
}
