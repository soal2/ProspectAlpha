export interface DashboardStats {
  totalAnalyses: number;
  evidenceCount: number;
  conclusions: number;
  crossValidationRate: number;
  hoursSaved: number;
  efficiency: number;
  avgSources: number;
  tokenK: number;
}

export const dashboardStats: DashboardStats = {
  totalAnalyses: 25,
  evidenceCount: 4218,
  conclusions: 312,
  crossValidationRate: 68,
  hoursSaved: 96,
  efficiency: 24,
  avgSources: 5.6,
  tokenK: 2480,
};
