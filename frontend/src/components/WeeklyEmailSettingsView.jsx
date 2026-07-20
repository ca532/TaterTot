import { Mail } from "lucide-react";
import WeeklyEmailSettings from "./WeeklyEmailSettings";

export default function WeeklyEmailSettingsView() {
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6">
      <div className="text-center mb-8">
        <div className="inline-flex items-center justify-center w-16 h-16 mb-4 rounded-full border-2 border-[#b8860b] text-[#b8860b]">
          <Mail className="w-8 h-8" />
        </div>
        <h2 className="text-2xl font-bold text-gray-900 mb-2">Weekly Email Settings</h2>
      </div>

      <WeeklyEmailSettings />
    </div>
  );
}
