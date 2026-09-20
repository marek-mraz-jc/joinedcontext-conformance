*** Settings ***
Documentation     Shared keywords for the ETSI GS CIM 009 V1.9.1 suites (TS-05, TS-06).
...               ${NGSILD_URL} is the NGSI-LD API root of the system under test, e.g.
...               https://<host>/cs/<space>/ngsi-ld/v1 for a joinedcontext Context Space (SP-03).
Library           RequestsLibrary
Library           Collections
Library           String
Library           DateTime

*** Variables ***
${NGSILD_URL}         http://localhost:8080/ngsi-ld/v1
${TENANT}             ${EMPTY}
${TOKEN}              ${EMPTY}
${PROJECTED_URL}      ${EMPTY}
${FEDERATED_URL}      ${EMPTY}
# The two middle segments of every id this suite mints. A bare broker takes any syntactically
# valid URN, but a Context Gateway refuses an id whose domain and space are not the ones the
# endpoint serves (SP-02, GW20), so a run against a deployment sets these to that deployment's
# values and the fixture entities pass the write guard instead of failing it.
${ORG_DOMAIN}         conformance
${SPACE}              smoke
${CORE_CONTEXT}       https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context-v1.9.jsonld
${SESSION}            sut
@{CREATED_ENTITIES}

*** Keywords ***
Open Custom NGSI-LD Session
    [Documentation]    Open a named RequestsLibrary session for a specific NGSI-LD URL.
    [Arguments]    ${session_name}    ${url}
    ${headers}=    NGSI-LD Headers
    Create Session    ${session_name}    ${url}    headers=${headers}    disable_warnings=${TRUE}

Get From Custom Session
    [Documentation]    GET ${path} on a specified session, returning the response object.
    [Arguments]    ${session_name}    ${path}    ${accept}=application/ld+json    &{params}
    ${headers}=    NGSI-LD Headers    accept=${accept}
    ${response}=    GET On Session    ${session_name}    ${path}    headers=${headers}    params=${params}    expected_status=any
    RETURN    ${response}

Open NGSI-LD Session
    [Documentation]    Session against ${NGSILD_URL}. Never raises on error status: the tests
    ...                assert the status themselves (CIM 009 clause 6.3.2).
    ${headers}=    NGSI-LD Headers
    Create Session    ${SESSION}    ${NGSILD_URL}    headers=${headers}    disable_warnings=${TRUE}
    ${entities}=    Create List
    Set Suite Variable    ${CREATED_ENTITIES}    ${entities}

Teardown NGSI-LD Session
    [Documentation]    Delete all entities registered during the suite execution. Failures are ignored.
    FOR    ${id}    IN    @{CREATED_ENTITIES}
        Run Keyword And Ignore Error    Delete On Session    ${SESSION}    /entities/${id}    expected_status=any
    END
    Delete All Sessions

Register Created Entity
    [Documentation]    Track entity id so Suite Teardown can purge it.
    [Arguments]    ${id}
    Append To List    ${CREATED_ENTITIES}    ${id}

NGSI-LD Headers
    [Documentation]    Accept application/ld+json plus optional tenant, bearer token, and custom accept/link.
    [Arguments]    ${content_type}=${EMPTY}    ${accept}=application/ld+json    ${link}=${EMPTY}
    ${headers}=    Create Dictionary    Accept=${accept}
    IF    '${content_type}' != '${EMPTY}'
        Set To Dictionary    ${headers}    Content-Type=${content_type}
    END
    IF    '${link}' != '${EMPTY}'
        Set To Dictionary    ${headers}    Link=${link}
    END
    IF    '${TENANT}' != '${EMPTY}'
        Set To Dictionary    ${headers}    NGSILD-Tenant=${TENANT}
    END
    IF    '${TOKEN}' != '${EMPTY}'
        Set To Dictionary    ${headers}    Authorization=Bearer ${TOKEN}
    END
    RETURN    ${headers}

Get From SUT
    [Documentation]    GET ${path} on the system under test, error statuses included.
    [Arguments]    ${path}    ${accept}=application/ld+json    &{params}
    ${headers}=    NGSI-LD Headers    accept=${accept}
    ${response}=    GET On Session    ${SESSION}    ${path}    headers=${headers}    params=${params}    expected_status=any
    RETURN    ${response}

Create Entity In SUT
    [Documentation]    POST /entities with JSON-LD payload, registering entity for teardown.
    [Arguments]    ${payload}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    POST On Session    ${SESSION}    /entities    json=${payload}    headers=${headers}    expected_status=any
    IF    ${response.status_code} == 201
        ${id}=    Get From Dictionary    ${payload}    id
        Register Created Entity    ${id}
    END
    RETURN    ${response}

Should Be NGSI-LD Error
    [Documentation]    CIM 009 clause 6.3.3 and clause 5.5.3: an error response carries
    ...                Content-Type application/json and an RFC 7807 object including at least
    ...                the terms type, title and detail, where "type" is one of the error URIs of
    ...                Table 6.3.2-1 that clause 6.3.2 maps to this status. More than one
    ...                ${error_types} value is passed only where the clause itself leaves the
    ...                choice open (e.g. 6.3.13 says just "400 BadRequest").
    [Arguments]    ${response}    ${status}    @{error_types}
    Should Be Equal As Integers    ${response.status_code}    ${status}
    ${content_type}=    Get From Dictionary    ${response.headers}    Content-Type
    Should Contain    ${content_type}    application/json
    ${body}=    Set Variable    ${response.json()}
    Dictionary Should Contain Key    ${body}    type
    Dictionary Should Contain Key    ${body}    title
    Dictionary Should Contain Key    ${body}    detail
    ${expected}=    Evaluate    ['https://uri.etsi.org/ngsi-ld/errors/' + t for t in $error_types]
    List Should Contain Value    ${expected}    ${body}[type]

Random Entity Id
    [Documentation]    A syntactically valid URN per SP-02 and CIM 009 clause 4.5.4:
    ...                urn:ngsi-ld:{Type}:{orgDomain}:{space}:{localId}. The domain and the space
    ...                come from ${ORG_DOMAIN} and ${SPACE} so the id belongs to the system under
    ...                test; against a gateway an id from somewhere else is a 400, not an entity.
    [Arguments]    ${type}=AirQualityObserved
    ${suffix}=    Generate Random String    8    [LOWER][NUMBERS]
    RETURN    urn:ngsi-ld:${type}:${ORG_DOMAIN}:${SPACE}:${suffix}

Build Sensor Entity
    [Documentation]    Build a JSON-LD AirQualityObserved entity with varied property types, geo location, and diacritics.
    [Arguments]    ${id}    ${type}=AirQualityObserved    ${lon}=19.15    ${lat}=48.74    ${pm10}=28.5    ${desc}=Banská Bystrica - Námestie SNP
    ${payload}=    Create Dictionary
    ...    id=${id}
    ...    type=${type}
    ...    pm10=${{ {'type': 'Property', 'value': ${pm10}} }}
    ...    description=${{ {'type': 'Property', 'value': '${desc}'} }}
    ...    location=${{ {'type': 'GeoProperty', 'value': {'type': 'Point', 'coordinates': [${lon}, ${lat}]}} }}
    ...    @context=${CORE_CONTEXT}
    RETURN    ${payload}

Get ISO Timestamp
    [Documentation]    Generate RFC 3339 timestamp with UTC Z timezone and relative offset in seconds.
    [Arguments]    ${offset_seconds}=0
    ${increment}=    Set Variable    ${offset_seconds} seconds
    ${ts}=    Get Current Date    time_zone=UTC    increment=${increment}    result_format=%Y-%m-%dT%H:%M:%SZ
    RETURN    ${ts}

Append Entity Attributes
    [Documentation]    POST /entities/{id}/attrs appending or updating temporal attributes (CIM 009 clause 6.6.3.1).
    [Arguments]    ${id}    ${payload}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    POST On Session    ${SESSION}    /entities/${id}/attrs    json=${payload}    headers=${headers}    expected_status=any
    RETURN    ${response}

Build AirQuality Entity
    [Documentation]    Build an AirQualityObserved entity conforming to the space data model.
    [Arguments]    ${id}    ${temp}=20.0    ${humidity}=50.0    ${name}=Stanica Centrum    ${lon}=19.15    ${lat}=48.74
    ${payload}=    Create Dictionary
    ...    id=${id}
    ...    type=AirQualityObserved
    ...    name=${{ {'type': 'Property', 'value': '${name}'} }}
    ...    temperature=${{ {'type': 'Property', 'value': float(${temp})} }}
    ...    relativeHumidity=${{ {'type': 'Property', 'value': float(${humidity})} }}
    ...    location=${{ {'type': 'GeoProperty', 'value': {'type': 'Point', 'coordinates': [float(${lon}), float(${lat})]}} }}
    ...    @context=${CORE_CONTEXT}
    RETURN    ${payload}

Build Device Entity
    [Documentation]    Build a Device entity conforming to the space data model.
    [Arguments]    ${id}    ${name}=Merač-01    ${battery}=95.0    ${lon}=19.15    ${lat}=48.74
    ${payload}=    Create Dictionary
    ...    id=${id}
    ...    type=Device
    ...    name=${{ {'type': 'Property', 'value': '${name}'} }}
    ...    batteryLevel=${{ {'type': 'Property', 'value': float(${battery})} }}
    ...    location=${{ {'type': 'GeoProperty', 'value': {'type': 'Point', 'coordinates': [float(${lon}), float(${lat})]}} }}
    ...    @context=${CORE_CONTEXT}
    RETURN    ${payload}

Build Temporal Observation Payload
    [Documentation]    Build an attribute fragment containing observedAt for temperature and relativeHumidity.
    [Arguments]    ${temp}    ${humidity}    ${observed_at}
    ${payload}=    Create Dictionary
    ...    temperature=${{ {'type': 'Property', 'value': float(${temp}), 'observedAt': '${observed_at}'} }}
    ...    relativeHumidity=${{ {'type': 'Property', 'value': float(${humidity}), 'observedAt': '${observed_at}'} }}
    ...    @context=${CORE_CONTEXT}
    RETURN    ${payload}

Build Device Observation Payload
    [Documentation]    Build an attribute fragment containing observedAt for batteryLevel.
    [Arguments]    ${battery}    ${observed_at}
    ${payload}=    Create Dictionary
    ...    batteryLevel=${{ {'type': 'Property', 'value': float(${battery}), 'observedAt': '${observed_at}'} }}
    ...    @context=${CORE_CONTEXT}
    RETURN    ${payload}
