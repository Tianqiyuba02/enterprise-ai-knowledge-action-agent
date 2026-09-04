"use client";

import {
  BookOpenText,
  CalendarDays,
  House,
  Headphones,
  MessageCircleMore,
  Rows3,
  Info,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Home", mobileLabel: "Home", icon: House, exact: true },
  { href: "/assistant", label: "Assistant", mobileLabel: "Assistant", icon: MessageCircleMore },
  { href: "/leave", label: "My leave", mobileLabel: "Leave", icon: CalendarDays },
  { href: "/it", label: "IT Support", mobileLabel: "IT", icon: Headphones },
  { href: "/requests", label: "My requests", mobileLabel: "Requests", icon: Rows3 },
  { href: "/policies", label: "Policy library", mobileLabel: "Policies", icon: BookOpenText },
  { href: "/about", label: "About", mobileLabel: "About", icon: Info },
];

export function SidebarNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Portal navigation" className="portal-nav">
      {links.map(({ href, label, mobileLabel, icon: Icon, exact }) => {
        const active = exact ? pathname === href : pathname.startsWith(href);
        return (
          <Link
            className="portal-nav-link"
            data-active={active || undefined}
            href={href}
            key={href}
          >
            <Icon aria-hidden="true" size={18} strokeWidth={1.8} />
            <span className="nav-label-desktop">{label}</span>
            <span className="nav-label-mobile">{mobileLabel}</span>
          </Link>
        );
      })}
    </nav>
  );
}
