*** Settings ***
Documentation     ETSI GS CIM 009 V1.9.1 smoke suite: the clauses every joinedcontext Context
...               Space surface must satisfy before a release is shown (TS-05, TS-06, SP-03).
...               Extended by T-0055; the full Testing Task Force suite runs from T-0054.
Resource          resources/ngsi_ld.resource
Suite Setup       Setup Fixture Entities
Suite Teardown    Teardown NGSI-LD Session
Force Tags        etsi    smoke

*** Variables ***
${SETUP_ID_1}     ${EMPTY}
${SETUP_ID_2}     ${EMPTY}
${SETUP_ID_FAR}   ${EMPTY}

*** Keywords ***
Setup Fixture Entities
    Open NGSI-LD Session
    ${id1}=    Random Entity Id    AirQualityObserved
    ${id2}=    Random Entity Id    AirQualityObserved
    ${id_far}=    Random Entity Id    Device
    Set Suite Variable    ${SETUP_ID_1}    ${id1}
    Set Suite Variable    ${SETUP_ID_2}    ${id2}
    Set Suite Variable    ${SETUP_ID_FAR}    ${id_far}
    ${e1}=    Build Sensor Entity    ${id1}    AirQualityObserved    19.146    48.736    25.5    Stanica Štadión
    ${e2}=    Build Sensor Entity    ${id2}    AirQualityObserved    19.152    48.742    42.0    Stanica Sásová
    ${efar}=    Build Sensor Entity    ${id_far}    Device    21.250    48.716    12.0    Merač Košice
    ${r1}=    Create Entity In SUT    ${e1}
    Should Be Equal As Integers    ${r1.status_code}    201
    ${r2}=    Create Entity In SUT    ${e2}
    Should Be Equal As Integers    ${r2.status_code}    201
    ${rfar}=    Create Entity In SUT    ${efar}
    Should Be Equal As Integers    ${rfar.status_code}    201

*** Test Cases ***
CIM009 6.3.2 Unknown Entity Id Is ResourceNotFound 404
    [Documentation]    Table 6.3.2-1 maps errors/ResourceNotFound to HTTP 404, and clause 6.5.3.1
    ...                serves Entity retrieval by id. An id that was never created must not be
    ...                answered with 200 or with a bare 404 without a problem body.
    ${id}=    Random Entity Id
    ${response}=    Get From SUT    /entities/${id}
    Should Be NGSI-LD Error    ${response}    404    ResourceNotFound

CIM009 6.3.2 Unsupported Media Type Is Rejected With 415
    [Documentation]    Clause 6.3.2: "Unsupported Media Type" (415) shall be raised when the
    ...                request payload Content-Type is neither application/json nor
    ...                application/ld+json. Asserted on entity creation (clause 6.4.3.1).
    ${headers}=    NGSI-LD Headers    content_type=text/csv
    ${response}=    POST On Session    ${SESSION}    /entities    data=id,type${\n}1,x
    ...    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    415

CIM009 6.4.3.1 Create Entity Returns 201 With Location Header
    [Documentation]    Clause 6.4.3.1: successful entity creation yields 201 Created and
    ...                HTTP header Location containing the URI of the created resource.
    ${id}=    Random Entity Id
    ${payload}=    Build Sensor Entity    ${id}    AirQualityObserved    19.15    48.74    18.4    Centrum mesta Žilina
    ${response}=    Create Entity In SUT    ${payload}
    Should Be Equal As Integers    ${response.status_code}    201
    Dictionary Should Contain Key    ${response.headers}    Location
    Should Contain    ${response.headers}[Location]    ${id}

CIM009 6.4.3.1 Duplicate Entity Creation Returns AlreadyExists 409
    [Documentation]    Clause 6.4.3.1 & Table 6.3.2-1: Attempting to create an Entity when
    ...                the Entity already exists shall return AlreadyExists 409.
    ${payload}=    Build Sensor Entity    ${SETUP_ID_1}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    POST On Session    ${SESSION}    /entities    json=${payload}
    ...    headers=${headers}    expected_status=any
    Should Be NGSI-LD Error    ${response}    409    AlreadyExists

CIM009 6.5.3.1 Retrieve Entity By Id Returns Normalized Entity
    [Documentation]    Clause 6.5.3.1: GET /entities/{entityId} returns the Entity representation
    ...                including its id, type, and defined attributes.
    ${response}=    Get From SUT    /entities/${SETUP_ID_1}
    Should Be Equal As Integers    ${response.status_code}    200
    ${body}=    Set Variable    ${response.json()}
    Should Be Equal As Strings    ${body}[id]    ${SETUP_ID_1}
    Should Be Equal As Strings    ${body}[type]    AirQualityObserved
    Dictionary Should Contain Key    ${body}    pm10
    Should Be Equal As Strings    ${body}[pm10][type]    Property

CIM009 6.5.3.1 Retrieve Entity Single Attribute Projection
    [Documentation]    Clause 6.5.3.1: Query parameter attrs restricts returned representation
    ...                to the requested attribute list and entity metadata.
    ${response}=    Get From SUT    /entities/${SETUP_ID_1}    attrs=description
    Should Be Equal As Integers    ${response.status_code}    200
    ${body}=    Set Variable    ${response.json()}
    Dictionary Should Contain Key    ${body}    description
    Dictionary Should Not Contain Key    ${body}    pm10

CIM009 6.7.3.1 Partial Attribute Update Returns 204
    [Documentation]    Clause 6.7.3.1: PATCH /entities/{entityId}/attrs/{attrId} updates
    ...                the target attribute and returns 204 No Content.
    ${patch}=    Create Dictionary    type=Property    value=${99.9}
    ${headers}=    NGSI-LD Headers    content_type=application/json    link=<${CORE_CONTEXT}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"
    ${response}=    PATCH On Session    ${SESSION}    /entities/${SETUP_ID_1}/attrs/pm10
    ...    json=${patch}    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    204
    ${check}=    Get From SUT    /entities/${SETUP_ID_1}    attrs=pm10
    Should Be Equal As Numbers    ${check.json()}[pm10][value]    99.9

CIM009 6.6.3.1 Append Attributes Returns 204
    [Documentation]    Clause 6.6.3.1: POST /entities/{entityId}/attrs appends new attributes
    ...                to an existing entity and returns 204 No Content.
    ${rel_target}=    Random Entity Id    Station
    ${attrs}=    Create Dictionary
    ...    controlledBy=${{ {'type': 'Relationship', 'object': '${rel_target}'} }}
    ...    @context=${CORE_CONTEXT}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    POST On Session    ${SESSION}    /entities/${SETUP_ID_1}/attrs
    ...    json=${attrs}    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    204
    ${check}=    Get From SUT    /entities/${SETUP_ID_1}
    Dictionary Should Contain Key    ${check.json()}    controlledBy
    Should Be Equal As Strings    ${check.json()}[controlledBy][object]    ${rel_target}

CIM009 6.5.3.4 Merge Entity Returns 204
    [Documentation]    Clause 6.5.3.4: PATCH /entities/{entityId} performs a merge patch
    ...                on entity attributes and returns 204 No Content.
    ${long_note}=    Generate Random String    120    [LETTERS]
    ${fragment}=    Create Dictionary
    ...    note=${{ {'type': 'Property', 'value': '${long_note}'} }}
    ...    @context=${CORE_CONTEXT}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    PATCH On Session    ${SESSION}    /entities/${SETUP_ID_1}
    ...    json=${fragment}    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    204
    ${check}=    Get From SUT    /entities/${SETUP_ID_1}
    Should Be Equal As Strings    ${check.json()}[note][value]    ${long_note}

CIM009 6.5.3.3 Replace Entity Returns 204
    [Documentation]    Clause 6.5.3.3: PUT /entities/{entityId} replaces existing entity
    ...                attributes with the provided payload and returns 204 No Content.
    ${replaced}=    Create Dictionary
    ...    id=${SETUP_ID_2}
    ...    type=AirQualityObserved
    ...    co2=${{ {'type': 'Property', 'value': 415.2} }}
    ...    @context=${CORE_CONTEXT}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    PUT On Session    ${SESSION}    /entities/${SETUP_ID_2}
    ...    json=${replaced}    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    204
    ${check}=    Get From SUT    /entities/${SETUP_ID_2}
    Dictionary Should Contain Key    ${check.json()}    co2
    Dictionary Should Not Contain Key    ${check.json()}    description

CIM009 6.7.3.2 Attribute Deletion Returns 204
    [Documentation]    Clause 6.7.3.2: DELETE /entities/{entityId}/attrs/{attrId} deletes
    ...                the attribute and returns 204 No Content.
    ${headers}=    NGSI-LD Headers
    ${response}=    DELETE On Session    ${SESSION}    /entities/${SETUP_ID_2}/attrs/co2
    ...    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    204
    ${check}=    Get From SUT    /entities/${SETUP_ID_2}
    Dictionary Should Not Contain Key    ${check.json()}    co2

CIM009 6.5.3.2 Entity Deletion Returns 204 And Follow-Up Retrieval Is 404
    [Documentation]    Clause 6.5.3.2: DELETE /entities/{entityId} removes the entity returning 204,
    ...                and subsequent retrieval returns ResourceNotFound 404 per clause 6.5.3.1.
    ${temp_id}=    Random Entity Id
    ${entity}=    Build Sensor Entity    ${temp_id}
    ${create}=    Create Entity In SUT    ${entity}
    Should Be Equal As Integers    ${create.status_code}    201
    ${headers}=    NGSI-LD Headers
    ${delete}=    DELETE On Session    ${SESSION}    /entities/${temp_id}    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${delete.status_code}    204
    ${get_res}=    Get From SUT    /entities/${temp_id}
    Should Be NGSI-LD Error    ${get_res}    404    ResourceNotFound

CIM009 6.14.3.1 Batch Entity Creation Returns 201
    [Documentation]    Clause 6.14.3.1: POST /entityOperations/create creates a batch of entities
    ...                and returns 201 Created with array of created entity ids.
    ${b1}=    Random Entity Id    WeatherObserved
    ${b2}=    Random Entity Id    WeatherObserved
    ${e1}=    Build Sensor Entity    ${b1}    WeatherObserved    19.14    48.73    15.0    Zvolen
    ${e2}=    Build Sensor Entity    ${b2}    WeatherObserved    19.16    48.75    17.5    Sliač
    ${batch}=    Create List    ${e1}    ${e2}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    POST On Session    ${SESSION}    /entityOperations/create
    ...    json=${batch}    headers=${headers}    expected_status=any
    Should Be Equal As Integers    ${response.status_code}    201
    Register Created Entity    ${b1}
    Register Created Entity    ${b2}
    ${res_list}=    Set Variable    ${response.json()}
    List Should Contain Value    ${res_list}    ${b1}
    List Should Contain Value    ${res_list}    ${b2}

CIM009 6.4.3.2 Query Entities By Type Returns Matching Array
    [Documentation]    Clause 6.4.3.2: GET /entities?type={typeName} queries entities by type.
    ${response}=    Get From SUT    /entities    type=WeatherObserved
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    FOR    ${item}    IN    @{items}
        Should Be Equal As Strings    ${item}[type]    WeatherObserved
    END

CIM009 6.4.3.2 Query Entities By Id List
    [Documentation]    Table 6.4.3.2-1: id takes a comma-separated list of entity identifiers, and
    ...                at least one among type, attrs, q or georel shall be present with it. The two
    ...                fixture entities named come back and the third fixture entity, of another
    ...                type and not named, does not.
    ${response}=    Get From SUT    /entities    type=AirQualityObserved    id=${SETUP_ID_1},${SETUP_ID_2}
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    ${len}=    Get Length    ${items}
    Should Be Equal As Integers    ${len}    2
    ${ids}=    Create List
    FOR    ${item}    IN    @{items}
        Append To List    ${ids}    ${item}[id]
    END
    Should Contain    ${ids}    ${SETUP_ID_1}
    Should Contain    ${ids}    ${SETUP_ID_2}
    Should Not Contain    ${ids}    ${SETUP_ID_FAR}

CIM009 5.7.2.4 Id List Alone Is BadRequestData 400
    [Documentation]    Clause 5.7.2.4 requires at least one of an Entity Type selector, an attribute
    ...                list, a query, a geoquery or local scope, and raises BadRequestData when none
    ...                is provided (too wide query). A list of entity identifiers is none of those,
    ...                so the same request without the type selector must be refused. Refusing a
    ...                too-wide query is also what keeps a caller from walking a whole tenant.
    ${response}=    Get From SUT    /entities    id=${SETUP_ID_1},${SETUP_ID_2}
    Should Be NGSI-LD Error    ${response}    400    BadRequestData

CIM009 6.4.3.2 Query Entities Filter By Attribute Value With q
    [Documentation]    Clause 6.4.3.2: Query parameter q filters entities matching attribute expressions.
    ${response}=    Get From SUT    /entities    type=AirQualityObserved    q=pm10>30
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    Should Not Be Empty    ${items}
    FOR    ${item}    IN    @{items}
        ${val}=    Set Variable    ${item}[pm10][value]
        Should Be True    ${val} > 30
    END

CIM009 6.4.3.2 Geoquery Near Point
    [Documentation]    Clause 6.4.3.2: Georel near filter matches entities located within maxDistance of geometry.
    ${response}=    Get From SUT    /entities    georel=near;maxDistance==2000    geometry=Point    coordinates=[19.15,48.74]
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    ${found}=    Set Variable    ${FALSE}
    FOR    ${item}    IN    @{items}
        IF    '${item}[id]' == '${SETUP_ID_1}'
            ${found}=    Set Variable    ${TRUE}
        END
    END
    Should Be True    ${found}

CIM009 6.4.3.2 Query Entities By idPattern
    [Documentation]    Table 6.4.3.2-1: idPattern selects entities whose identifier matches the
    ...                regular expression, and at least one among type, attrs, q or georel shall be
    ...                present with it. Every entity returned matches the pattern.
    # The org domain carries dots, and an unescaped dot would widen the pattern the
    # answers are then asserted against, so the literal is escaped before it is a regex.
    ${pattern}=    Evaluate    ".*:AirQualityObserved:" + re.escape($ORG_DOMAIN) + ":.*"    modules=re
    ${response}=    Get From SUT    /entities    type=AirQualityObserved    idPattern=${pattern}
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    Should Not Be Empty    ${items}
    FOR    ${item}    IN    @{items}
        Should Match Regexp    ${item}[id]    ${pattern}
    END

CIM009 5.7.2.4 idPattern Alone Is BadRequestData 400
    [Documentation]    Clause 5.7.2.4: an identifier pattern is not an Entity Type selector, an
    ...                attribute list, a query, a geoquery or a local scope, so idPattern on its own
    ...                is the too-wide query the clause refuses with BadRequestData.
    ${response}=    Get From SUT    /entities    idPattern=.*
    Should Be NGSI-LD Error    ${response}    400    BadRequestData

CIM009 6.4.3.2 Query Matching Nothing Returns Empty Array 200
    [Documentation]    Clause 6.4.3.2: A query returning zero results responds with an empty
    ...                JSON array and HTTP status 200, never ResourceNotFound 404.
    ${response}=    Get From SUT    /entities    type=NonexistentTypeZeroMatch
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    ${len}=    Get Length    ${items}
    Should Be Equal As Integers    ${len}    0

CIM009 6.3.7 Simplified Representation format Simplified
    [Documentation]    Table 6.3.7-1 & clause 4.5.4: format=simplified returns key-value pairs
    ...                where attribute objects are replaced directly by their values.
    ${normalized}=    Get From SUT    /entities/${SETUP_ID_1}
    Should Be Equal As Integers    ${normalized.status_code}    200
    ${response}=    Get From SUT    /entities/${SETUP_ID_1}    format=simplified
    Should Be Equal As Integers    ${response.status_code}    200
    ${body}=    Set Variable    ${response.json()}
    Should Be Equal As Strings    ${body}[id]    ${SETUP_ID_1}
    Should Be Equal As Numbers    ${body}[pm10]    ${normalized.json()}[pm10][value]
    Should Be Equal As Strings    ${body}[description]    ${normalized.json()}[description][value]

CIM009 6.3.11 Include System Attributes options sysAttrs
    [Documentation]    Table 6.3.11-1: options=sysAttrs includes system-generated temporal
    ...                attributes createdAt and modifiedAt in the response body.
    ${response}=    Get From SUT    /entities/${SETUP_ID_1}    options=sysAttrs
    Should Be Equal As Integers    ${response.status_code}    200
    ${body}=    Set Variable    ${response.json()}
    Dictionary Should Contain Key    ${body}    createdAt
    Dictionary Should Contain Key    ${body}    modifiedAt

CIM009 6.3.13 Count Parameter Sets NGSILD-Results-Count Header
    [Documentation]    Table 6.3.13-1: count=true sets the HTTP response header
    ...                NGSILD-Results-Count to the total number of matching items.
    ${response}=    Get From SUT    /entities    type=AirQualityObserved    count=true    limit=1
    Should Be Equal As Integers    ${response.status_code}    200
    Dictionary Should Contain Key    ${response.headers}    NGSILD-Results-Count
    ${count}=    Convert To Integer    ${response.headers}[NGSILD-Results-Count]
    Should Be True    ${count} >= 2

CIM009 6.3.10 Pagination Limit Yields Link Rel Next
    [Documentation]    Clause 6.3.10: Pointers to further pages are serialized in the Link header
    ...                with rel="next" when more results exist.
    ${response}=    Get From SUT    /entities    type=AirQualityObserved    limit=1
    Should Be Equal As Integers    ${response.status_code}    200
    Dictionary Should Contain Key    ${response.headers}    Link
    Should Contain    ${response.headers}[Link]    rel="next"

CIM009 6.3.15 Retrieve Entity With GeoJSON Representation
    [Documentation]    Clause 6.3.15: Accept: application/geo+json returns entity rendered as
    ...                a GeoJSON Feature with geometry and properties.
    ${response}=    Get From SUT    /entities/${SETUP_ID_1}    accept=application/geo+json
    Should Be Equal As Integers    ${response.status_code}    200
    ${body}=    Set Variable    ${response.json()}
    Should Be Equal As Strings    ${body}[type]    Feature
    Dictionary Should Contain Key    ${body}    geometry
    Dictionary Should Contain Key    ${body}    properties
    Should Be Equal As Strings    ${body}[geometry][type]    Point

CIM009 6.3.13 Limit Zero Without Count Is BadRequestData 400
    [Documentation]    Table 6.3.10-1 & clause 6.3.13: Setting limit to zero without including
    ...                the count URI parameter results in a BadRequestData 400 error.
    ${response}=    Get From SUT    /entities    limit=0
    Should Be NGSI-LD Error    ${response}    400    BadRequestData    InvalidRequest

CIM009 6.3.5 Context In Body With Content-Type Application Json Is BadRequestData 400
    [Documentation]    Clause 6.3.5: For Content-Type application/json, if the request payload
    ...                body contains an @context term, an error of type BadRequestData shall be raised.
    ${id}=    Random Entity Id
    ${payload}=    Create Dictionary
    ...    id=${id}
    ...    type=AirQualityObserved
    ...    @context=${CORE_CONTEXT}
    ${headers}=    NGSI-LD Headers    content_type=application/json
    ${response}=    POST On Session    ${SESSION}    /entities    json=${payload}    headers=${headers}    expected_status=any
    Should Be NGSI-LD Error    ${response}    400    BadRequestData

CIM009 6.3.5 Link Header With Content-Type Application Ld Json Is BadRequestData 400
    [Documentation]    Clause 6.3.5: For Content-Type application/ld+json, the presence of a
    ...                JSON-LD Link header in that request shall result in BadRequestData 400.
    ${id}=    Random Entity Id
    ${payload}=    Build Sensor Entity    ${id}
    ${link}=    Set Variable    <${CORE_CONTEXT}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json    link=${link}
    ${response}=    POST On Session    ${SESSION}    /entities    json=${payload}    headers=${headers}    expected_status=any
    Should Be NGSI-LD Error    ${response}    400    BadRequestData

CIM009 6.4.3.1 Missing Type In Payload Is BadRequestData 400
    [Documentation]    Clause 5.6.1 & Table 6.3.2-1: An Entity payload missing mandatory
    ...                element type is rejected with BadRequestData 400.
    ${id}=    Random Entity Id
    ${payload}=    Create Dictionary    id=${id}    @context=${CORE_CONTEXT}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    POST On Session    ${SESSION}    /entities    json=${payload}    headers=${headers}    expected_status=any
    Should Be NGSI-LD Error    ${response}    400    BadRequestData

CIM009 6.4.3.1 Non URI Entity Id Is BadRequestData 400
    [Documentation]    Clause 4.5.4 & Table 6.3.2-1: An entity identifier that is not a valid
    ...                URI is rejected with BadRequestData 400.
    ${payload}=    Create Dictionary    id=not-a-valid-uri    type=AirQualityObserved    @context=${CORE_CONTEXT}
    ${headers}=    NGSI-LD Headers    content_type=application/ld+json
    ${response}=    POST On Session    ${SESSION}    /entities    json=${payload}    headers=${headers}    expected_status=any
    Should Be NGSI-LD Error    ${response}    400    BadRequestData
