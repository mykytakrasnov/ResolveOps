import {
  FilmStripIcon,
  ShieldCheckIcon,
  TrayIcon,
} from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useLocation } from "react-router";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarInset,
  SidebarItem,
  SidebarLabel,
  SidebarProvider,
  SidebarSection,
  SidebarTrigger,
} from "@/components/ui/sidebar";

const navigation = [
  { label: "Cases", href: "/app/cases", icon: TrayIcon },
  { label: "Review", href: "/app/review", icon: ShieldCheckIcon },
  { label: "Replays", href: "/replays", icon: FilmStripIcon },
];

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();

  return (
    <SidebarProvider>
      <Sidebar collapsible="dock" intent="default">
        <SidebarHeader className="border-b">
          <a href="/app/cases" className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <ShieldCheckIcon aria-hidden weight="fill" />
            </span>
            <span className="flex flex-col in-data-[state=collapsed]:hidden">
              <span className="font-semibold leading-none">ResolveOps</span>
              <span className="mt-1 text-xs text-muted-foreground">
                AtlasFlow demo
              </span>
            </span>
          </a>
        </SidebarHeader>
        <SidebarContent>
          <SidebarSection label="Workspace">
            {navigation.map((item) => (
              <SidebarItem
                key={item.href}
                href={item.href}
                isCurrent={
                  item.href === "/app/cases"
                    ? location.pathname.startsWith("/app/cases") ||
                      location.pathname.startsWith("/app/runs")
                    : location.pathname.startsWith(item.href)
                }
                tooltip={item.label}
              >
                <item.icon aria-hidden />
                <SidebarLabel>{item.label}</SidebarLabel>
              </SidebarItem>
            ))}
          </SidebarSection>
        </SidebarContent>
        <SidebarFooter>
          <div className="flex w-full items-center gap-3 rounded-lg border border-sidebar-border p-3 in-data-[state=collapsed]:hidden">
            <span className="size-2 rounded-full bg-primary" aria-hidden />
            <div className="min-w-0">
              <p className="text-xs font-medium">Synthetic workspace</p>
              <p className="truncate text-xs text-muted-foreground">
                No real side effects
              </p>
            </div>
          </div>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset>
        <header className="sticky top-0 flex h-14 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur md:hidden">
          <SidebarTrigger aria-label="Open navigation" />
          <span className="font-semibold">ResolveOps</span>
        </header>
        {children}
      </SidebarInset>
    </SidebarProvider>
  );
}
