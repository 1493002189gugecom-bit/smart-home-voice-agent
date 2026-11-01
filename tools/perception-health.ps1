# Shared by the launcher and its offline configuration tests. No startup side effects.
function Assert-ServiceConfiguration {
    param([string]$Name, $Health, [string]$HomeBackend, [string]$ListeningMode)

    $restart = 'Stop the existing stack with tools\stop-perception.ps1 and restart with the requested settings.'
    if ($Name -eq 'home' -and $Health.backend -ne $HomeBackend) {
        throw "[home] backend mismatch: expected '$HomeBackend', actual '$($Health.backend)'. $restart"
    }
    if ($Name -eq 'voice') {
        if ($Health.listening_mode -ne $ListeningMode) {
            throw "[voice] listening_mode mismatch: expected '$ListeningMode', actual '$($Health.listening_mode)'. Check intent API configuration if hybrid fell back to wake. $restart"
        }
        if ($ListeningMode -eq 'hybrid' -and $Health.intent_status -ne 'ready') {
            throw "[voice] intent_status is '$($Health.intent_status)'; hybrid intent recognition is not ready. Check intent API configuration. $restart"
        }
    }
}
