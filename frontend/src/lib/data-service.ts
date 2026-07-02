import { USE_MOCK } from "./env";
import {
  analyses as mockAnalyses,
  agents as mockAgents,
  backtests as mockBacktests,
  dashboardStats as mockDashboardStats,
} from "@/mocks";

// Mock data fetchers (replace with real API calls when backend is ready)
async function fetchAnalyses(): Promise<typeof mockAnalyses> {
  return [];
}

async function fetchAnalysis(id: string): Promise<(typeof mockAnalyses)[number] | null> {
  return null;
}

async function fetchAgents(): Promise<typeof mockAgents> {
  return [];
}

async function fetchBacktests(): Promise<typeof mockBacktests> {
  return [];
}

async function fetchDashboardStats(): Promise<typeof mockDashboardStats> {
  return mockDashboardStats;
}

export async function getAnalyses() {
  if (USE_MOCK) return mockAnalyses;
  return fetchAnalyses();
}

export async function getAnalysis(id: string) {
  if (USE_MOCK) return mockAnalyses.find((a) => a.id === id) ?? null;
  return fetchAnalysis(id);
}

export async function getAgents() {
  if (USE_MOCK) return mockAgents;
  return fetchAgents();
}

export async function getBacktests() {
  if (USE_MOCK) return mockBacktests;
  return fetchBacktests();
}

export async function getDashboardStats() {
  if (USE_MOCK) return mockDashboardStats;
  return fetchDashboardStats();
}
