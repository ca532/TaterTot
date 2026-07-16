import { useEffect, useState } from "react";
import { Mail, Save } from "lucide-react";
import githubAPI from "../services/githubAPI";

export default function WeeklyEmailSettings() {
  const [topic, setTopic] = useState("finance");
  const [topicConfigs, setTopicConfigs] = useState([]);
  const [keywords, setKeywords] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  const keywordCount = keywords
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean).length;

  useEffect(() => {
    let mounted = true;

    const load = async () => {
      const [configRes, topicsRes] = await Promise.all([
        githubAPI.getWeeklyEmailConfig(),
        githubAPI.getTopicConfigs(),
      ]);
      if (!mounted) return;

      if (topicsRes.success) {
        setTopicConfigs(topicsRes.topics || []);
      }
      if (configRes.success) {
        setTopic(configRes.config.topic || "finance");
        setKeywords(configRes.config.keywords || "");
      }
    };

    load();
    return () => {
      mounted = false;
    };
  }, []);

  const save = async () => {
    setSaving(true);
    setStatus("");

    const res = await githubAPI.updateWeeklyEmailConfig({
      topic,
      keywords,
    });

    setSaving(false);

    if (!res.success) {
      setStatus(res.error || "Failed to save weekly email settings.");
      return;
    }

    setStatus("Weekly email settings saved.");
  };

  return (
    <div className="w-full max-w-3xl mx-auto mb-6 p-4 border-2 border-[#b8860b] rounded-lg bg-white text-left">
      <div className="flex items-center gap-2 mb-4">
        <Mail className="w-5 h-5 text-[#b8860b]" />
        <h3 className="text-base font-bold text-gray-900">Weekly Email Settings</h3>
      </div>

      <div className="mb-3">
        <label className="block text-sm font-semibold mb-1">Topic</label>
        <select
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          className="w-full p-2 border border-gray-300 rounded"
        >
          {topicConfigs.map((t) => (
            <option key={t.topic_name} value={t.topic_name}>
              {t.topic_name}
              {t.keyword_count ? ` (${t.keyword_count} keywords)` : ""}
            </option>
          ))}
        </select>
      </div>

      <label className="block text-sm font-semibold mb-1">Weekly Keywords Override</label>
      <textarea
        value={keywords}
        onChange={(e) => setKeywords(e.target.value)}
        rows={3}
        placeholder="Optional comma-separated keywords. Leave blank to use this topic's configured keywords."
        className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#b8860b] focus:border-[#b8860b] text-sm"
      />

      <div className="mt-2 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
        <p className="text-xs text-gray-500">
          {keywordCount === 0
            ? "Blank uses the selected topic's configured keywords."
            : `${keywordCount} keyword${keywordCount === 1 ? "" : "s"} selected.`}
        </p>

        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-[#b8860b] text-black font-semibold rounded disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          <Save className="w-4 h-4" />
          <span>{saving ? "Saving..." : "Save Weekly Settings"}</span>
        </button>
      </div>

      {status && (
        <p className={`mt-3 text-sm ${status.includes("Failed") ? "text-red-600" : "text-green-700"}`}>
          {status}
        </p>
      )}
    </div>
  );
}
