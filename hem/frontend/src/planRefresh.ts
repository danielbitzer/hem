import type { QueryClient } from "@tanstack/react-query";
import type { PlanResponse } from "./api";

/** After a config save the planner wakes and re-solves within a second or
 * two — but an immediate refetch races it and gets the OLD plan, leaving
 * banners (vacation, lifecycle) stale until the next 60 s poll. Poll briefly
 * until computed_at moves; give up quietly (e.g. HEM disabled, no plan). */
export async function refetchPlanUntilFresh(queryClient: QueryClient): Promise<void> {
  const before = queryClient.getQueryData<PlanResponse>(["plan"])?.computed_at;
  for (let attempt = 0; attempt < 5; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await queryClient.refetchQueries({ queryKey: ["plan"] });
    const after = queryClient.getQueryData<PlanResponse>(["plan"])?.computed_at;
    if (after && after !== before) return;
  }
}
