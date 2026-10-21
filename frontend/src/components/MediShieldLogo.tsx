"use client";

import React from "react";

export interface MediShieldLogoProps {
  size?: "xs" | "sm" | "md" | "lg" | "xl";
  showWordmark?: boolean;
  showBadge?: boolean;
  badgeText?: string;
  className?: string;
  iconClassName?: string;
}

const sizeMap = {
  xs: {
    iconSize: 24,
    textClass: "text-sm",
    badgeClass: "text-[9px] px-1.5 py-0.5",
    gap: "space-x-2",
  },
  sm: {
    iconSize: 32,
    textClass: "text-base",
    badgeClass: "text-[10px] px-2 py-0.5",
    gap: "space-x-2.5",
  },
  md: {
    iconSize: 40,
    textClass: "text-xl",
    badgeClass: "text-[11px] px-2.5 py-0.5",
    gap: "space-x-3",
  },
  lg: {
    iconSize: 50,
    textClass: "text-2xl",
    badgeClass: "text-xs px-3 py-1",
    gap: "space-x-3.5",
  },
  xl: {
    iconSize: 64,
    textClass: "text-3xl sm:text-4xl",
    badgeClass: "text-xs px-3.5 py-1",
    gap: "space-x-4",
  },
};

export function MediShieldLogo({
  size = "md",
  showWordmark = true,
  showBadge = false,
  badgeText = "v1.0",
  className = "",
  iconClassName = "",
}: MediShieldLogoProps) {
  const cfg = sizeMap[size] || sizeMap.md;

  return (
    <div className={`inline-flex items-center ${cfg.gap} select-none group ${className}`}>
      {/* Precision 3D Vector Shield Icon */}
      <div
        className={`relative shrink-0 flex items-center justify-center transition-all duration-200 group-hover:scale-105 group-hover:-translate-y-0.5 ${iconClassName}`}
        style={{ width: cfg.iconSize, height: cfg.iconSize }}
      >
        <svg
          viewBox="0 0 38 38"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="w-full h-full"
        >
          <defs>
            {/* Rich Ambient Soft Drop Shadow */}
            <filter id="ms-shield-shadow" x="0" y="0" width="38" height="38" filterUnits="userSpaceOnUse">
              <feDropShadow dx="0" dy="2" stdDeviation="1.8" floodColor="#1B639B" floodOpacity="0.32" />
            </filter>

            {/* Glowing Vital Pulse Filter */}
            <filter id="ms-pulse-glow" x="6" y="10" width="26" height="18" filterUnits="userSpaceOnUse">
              <feDropShadow dx="0" dy="0" stdDeviation="0.8" floodColor="#FF4F81" floodOpacity="0.6" />
            </filter>

            {/* Radiant Sky-to-Sapphire Medical Shield Gradient */}
            <linearGradient
              id="ms-shield-grad"
              x1="5"
              y1="3"
              x2="33"
              y2="35"
              gradientUnits="userSpaceOnUse"
            >
              <stop offset="0%" stopColor="#58B7ED" />
              <stop offset="45%" stopColor="#2F8ECB" />
              <stop offset="100%" stopColor="#1B639B" />
            </linearGradient>

            {/* Specular Crystalline Bevel Rim Highlight */}
            <linearGradient
              id="ms-inner-highlight"
              x1="19"
              y1="4.5"
              x2="19"
              y2="33"
              gradientUnits="userSpaceOnUse"
            >
              <stop offset="0%" stopColor="#FFFFFF" stopOpacity="0.45" />
              <stop offset="35%" stopColor="#FFFFFF" stopOpacity="0.18" />
              <stop offset="100%" stopColor="#FFFFFF" stopOpacity="0.02" />
            </linearGradient>

            {/* High-Tech Medical Rose Vital Waveform Gradient */}
            <linearGradient
              id="ms-pulse-grad"
              x1="8"
              y1="19"
              x2="30"
              y2="19"
              gradientUnits="userSpaceOnUse"
            >
              <stop offset="0%" stopColor="#FF85A5" />
              <stop offset="48%" stopColor="#F43F6E" />
              <stop offset="100%" stopColor="#BE185D" />
            </linearGradient>

            {/* Cross Drop Shadow for Beveled Depth */}
            <filter id="ms-cross-shadow" x="10" y="8" width="18" height="22" filterUnits="userSpaceOnUse">
              <feDropShadow dx="0" dy="1" stdDeviation="0.6" floodColor="#0F3B5F" floodOpacity="0.25" />
            </filter>
          </defs>

          {/* Outer Shield Hull with Ambient Elevation */}
          <path
            d="M19 2.5C24.8 2.5 32 5.5 32.5 6.4C33.1 10.2 33.6 22 19 35.5C4.4 22 4.9 10.2 5.5 6.4C6 5.5 13.2 2.5 19 2.5Z"
            fill="url(#ms-shield-grad)"
            filter="url(#ms-shield-shadow)"
          />

          {/* Frosted Glass Crystalline Inner Inset */}
          <path
            d="M19 4.8C24 4.8 29.8 7.3 30.3 8.1C30.7 11.5 31.1 20.8 19 32.2C6.9 20.8 7.3 11.5 7.7 8.1C8.2 7.3 14 4.8 19 4.8Z"
            fill="url(#ms-inner-highlight)"
          />

          {/* Clinical Medical Cross (Crisp Pure White with Soft Depth) */}
          <g filter="url(#ms-cross-shadow)">
            <path
              d="M16.5 10.5C16.5 9.67 17.17 9 18 9H20C20.83 9 21.5 9.67 21.5 10.5V14.5H25.5C26.33 14.5 27 15.17 27 16V18C27 18.83 26.33 19.5 25.5 19.5H21.5V23.5C21.5 24.33 20.83 25 20 25H18C17.17 25 16.5 24.33 16.5 23.5V19.5H12.5C11.67 19.5 11 18.83 11 18V16C11 15.17 11.67 14.5 12.5 14.5H16.5V10.5Z"
              fill="#FFFFFF"
            />
          </g>

          {/* Dynamic Vital Sign ECG Heartbeat Line in Medical Rose */}
          <path
            d="M8.5 17H13.5L15.8 12.8L19 22L21.8 14.5L24 17H29.5"
            stroke="url(#ms-pulse-grad)"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            filter="url(#ms-pulse-glow)"
          />

          {/* Active Adjudication Pulse Beacon */}
          <circle cx="19" cy="17" r="2.8" fill="#FF4F81" fillOpacity="0.25" />
          <circle cx="19" cy="17" r="1.4" fill="#FF4F81" />
        </svg>
      </div>

      {/* Brand Typography & Badge */}
      {showWordmark && (
        <div className="flex items-center space-x-2 min-w-0">
          <span className={`font-black ${cfg.textClass} tracking-tight select-none leading-none truncate`}>
            <span className="text-slate-900 dark:text-white">Medi</span>
            <span className="bg-gradient-to-r from-[#2072A7] via-[#359BE0] to-[#5DBAEF] dark:from-[#72BDEB] dark:via-[#9CD7FA] dark:to-[#CFEBFE] bg-clip-text text-transparent">
              Shield
            </span>
          </span>

          {showBadge && (
            <span
              className={`inline-flex items-center rounded-full bg-sky-soft/90 dark:bg-sky-deep/20 text-[#2072A7] dark:text-[#78BCE5] border border-sky/35 dark:border-sky/30 font-mono font-bold shrink-0 shadow-xs ${cfg.badgeClass}`}
            >
              <span className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse mr-1 inline-block shrink-0" />
              <span>{badgeText}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}
