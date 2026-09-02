"use client";

import {
  BookOpenText,
  CalendarDays,
  House,
  MessageCircleMore,
  Rows3,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Home", icon: House, exact: true },
  { href: "/assistant", label: "Assistant", icon: MessageCircleMore },
  { href: "/leave", label: "My leave", icon: CalendarDays },
  { href: "/requests", label: "My requests", icon: Rows3 },
  { href: "/policies", label: "Policy library", icon: BookOpenText },
];

export function SidebarNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Portal navigation" className="portal-nav">
      {links.map(({ href, label, icon: Icon, exact }) => {
        const active = exact ? pathname === href : pathname.startsWith(href);
        return (
          <Link
            className="portal-nav-link"
            data-active={active || undefined}
            href={href}
            key={href}
          >
            <Icon aria-hidden="true" size={18} strokeWidth={1.8} />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
