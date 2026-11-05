import { ConfigProvider, Select } from "antd";

type Option = { value: string; label: string };

/** Shared theme for upload metadata, filters, and the material editor. */
export default function MaterialSelect({ label, value, options, onChange, disabled = false }: {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  return <ConfigProvider theme={{
    token: { motion: false, colorText: "#193650", colorBorder: "#cbd4d7", colorBgElevated: "#fffefa", controlHeight: 44, fontSize: 14, borderRadius: 6 },
    components: { Select: { optionHeight: 40, optionPadding: "10px 12px", optionSelectedBg: "#e6eff3", optionSelectedColor: "#123c5c", optionActiveBg: "#f0f5f7", activeBorderColor: "#2b688d", hoverBorderColor: "#7aa3b8", activeOutlineColor: "#2b688d1f" } },
  }}>
    <Select className="material-select" aria-label={label} value={value} options={options}
      onChange={onChange} disabled={disabled} virtual={false} showSearch={false}
      classNames={{ popup: { root: "material-select-menu" } }}
      optionRender={option => <span className="material-select-option"><span>{option.label}</span><span aria-hidden="true">{option.value === value ? "✓" : ""}</span></span>}
      suffixIcon={<span className="material-select-chevron" aria-hidden="true" />}
    />
  </ConfigProvider>;
}
