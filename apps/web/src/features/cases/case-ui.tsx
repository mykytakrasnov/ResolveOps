import { Badge } from "@/components/ui/badge";

export function humanize(value: string) {
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function CategoryBadge({ category }: { category: string }) {
  return <Badge variant="secondary">{humanize(category)}</Badge>;
}

export function DifficultyBadge({ difficulty }: { difficulty: string }) {
  return <Badge variant="outline">{humanize(difficulty)}</Badge>;
}

export function ApprovalBadge({ required }: { required: boolean }) {
  return (
    <Badge variant={required ? "default" : "outline"}>
      {required ? "Approval expected" : "No approval expected"}
    </Badge>
  );
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
