import { WarningCircleIcon } from "@phosphor-icons/react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export function PageLoading({ label }: { label: string }) {
  return (
    <output className="flex flex-col gap-4" aria-label={label}>
      <span className="text-sm text-muted-foreground">{label}</span>
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-20 w-full" />
    </output>
  );
}

export function PageError({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: Error;
  onRetry?: () => void;
}) {
  return (
    <Alert variant="destructive">
      <WarningCircleIcon aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col items-start gap-3">
        <span>{error.message}</span>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
