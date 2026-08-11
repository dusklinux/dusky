import { With } from "ags"

type OptionalFeatureProps = {
  enabled: () => boolean
  render: () => JSX.Element
}

export default function OptionalFeature({ enabled, render }: OptionalFeatureProps) {
  return (
    <With value={enabled}>
      {(enabled) => {
        return enabled ? render() : <box visible={false} />
      }}
    </With>
  )
}
